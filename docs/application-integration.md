# Rivet trusted application integration

## Scope and ownership

This layer connects the independently maintained components without changing
their behavior:

- `rivet.reconciliation` normalizes trusted billing/usage observations and
  produces deterministic findings. It has no network or persistence effects.
- `rivet.aegis.verify_reconciliation` checks the serialized result against the
  original input independently. AEGIS PASS means evidence consistency only;
  it does not attest to Stripe provenance, authorize repair, or authorize a
  financial action.
- `rivet.application.RivetOrchestrator` owns the application state machine,
  idempotency, approval checks, and evidence association.
- `WorkflowStore` is the persistence seam. `SQLiteWorkflowStore` is the local
  test adapter. A production PostgreSQL adapter can implement the same
  interface after the Stripe Projects database blocker is resolved.
- `SandboxAdapter` is an interface only. A trusted adapter must call CreateOS
  with the intended sandbox policy. Generated code is not executed by this
  module or on the trusted host.

The API accepts `ReconciliationInput` built from an upstream adapter. The
repository does not claim these records came from live Stripe. An optional
`candidate_result` lets the trusted application check a result produced by a
separate worker; AEGIS must accept it before a repair task can be approved.
Without a candidate result, the application calls the existing reconciliation
engine itself.

## Typed contracts and flow

Public contracts include `Principal` / `PrincipalRole`, `RepairTask`,
`TaskRecord`, `SubmissionRecord`, `ExecutionResult`, `ExecutionEvidence`,
`WorkflowStore`, and `SandboxAdapter`.

1. The application serializes the input and computes the engine result (or
   accepts a candidate result for independent checking).
2. It stores the request and candidate findings as `PROPOSED`, using a hash of
   the idempotency key and a request digest. Repeating the same key and same
   payload returns the existing submission; using that key with different
   content raises `IdempotencyConflictError`.
3. AEGIS checks the input and result. PASS moves proposed tasks to `VERIFIED`;
   REJECT moves them to terminal `REJECTED`. Invalid/unverified tasks cannot
   be approved or executed.
4. Only a `HUMAN` principal may approve a verified task. The proposer cannot
   approve their own task. Verification never writes an approval.
5. Only a distinct `WORKER` principal can start an approved task through the
   sandbox adapter. The application records the attempt, exit code, stdout,
   stderr, metadata, and an integrity digest for the associated evidence.
6. Exit code zero produces `SUCCEEDED`; any other exit code or adapter error
   produces `FAILED`. In this foundation, SUCCEEDED means the adapter returned
   a successful execution result. It does not mean a patch was promoted or a
   billing repair was applied.

Allowed task transitions:

```text
PROPOSED -> VERIFIED -> APPROVED -> EXECUTING -> SUCCEEDED
    |           |                                  `-> FAILED
    `-> REJECTED `-> REJECTED
```

There is no generic public state setter. The store is a trusted application
dependency and must never be exposed to generated workers. Its state-changing
methods repeat role, approval, and transition checks. These controls are
application authorization, not a process or sandbox security boundary.

## Persistence

The SQLite adapter stores submissions and their canonical JSON input/result,
findings, AEGIS verification reports, task states, human approvals, repair
attempts, and execution evidence in separate related tables. Evidence retrieval
recomputes the payload SHA-256 and raises `EvidenceIntegrityError` if it
differs. This digest detects changes; it is not a signature or proof of who
produced the output.

SQLite is used only for local/integration tests and can run in memory. The
adapter is replaceable through `WorkflowStore`; no PostgreSQL service or
application tables are provisioned by this change. Deployments must protect
the store because input snapshots can contain customer and billing identifiers.
The upstream HTTP/authentication layer must derive principal IDs and roles from
authenticated identity; it must not trust a client-provided role.

## Security and operational boundary

- No Stripe API calls, Stripe credentials, database connection credentials,
  charges, refunds, or production mutations are implemented here.
- The orchestration layer does not send credentials to the worker. A future
  sandbox adapter must explicitly allowlist task input and secrets, configure
  and test egress policy, and preserve raw stdout/stderr as evidence without
  logging credentials. This code does not claim CreateOS isolation or network
  restrictions beyond the prior smoke-test observations.
- AEGIS validates reconciliation evidence, not the semantics or safety of a
  proposed source-code patch. Patch validation, promotion, and any financial
  remediation remain separate steps and require human approval.
- Human approval authorizes the next application execution step only; it does
  not authorize charges, refunds, or other financial mutation.

## Test

From WSL at `/mnt/c/Users/USER/Desktop/Rivet`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests
```

Integration tests cover reconciliation-to-AEGIS verification, rejected and
unverified findings, explicit approval, worker/proposer separation, invalid
transitions, execution failure handling, evidence association and integrity,
and duplicate submission idempotency. Existing reconciliation and AEGIS tests
remain unchanged.

## Remaining integration work

- Connect a trusted Stripe/Test Mode telemetry adapter and validate source
  provenance before constructing `ReconciliationInput`.
- Implement a PostgreSQL `WorkflowStore` after the database is actually
  provisioned; no live database connectivity is claimed.
- Implement and test a CreateOS `SandboxAdapter` that passes only allowlisted
  non-secret task data and returns genuine execution output.
- Define a trusted patch verifier for proposed `billing_bridge.py` changes.
  AEGIS's current remit is reconciliation-evidence consistency.
- Add authenticated API/UI endpoints for approvals. The current `Principal`
  value is an internal typed contract, not an authentication mechanism.
- Add promotion only after a separate trusted patch verification and human
  approval flow. This implementation performs no source-file promotion.
