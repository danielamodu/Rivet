# Rivet deterministic reconciliation foundation

## Scope and existing state

Before this change, the repository contained the CreateOS Sandbox smoke-test
script and reports for CreateOS and Stripe Projects. It had no billing or usage
reconciliation code, input schema, Python package configuration, or test suite.
This implementation adds a pure-Python reconciliation vertical slice. It does
not connect to Stripe, a database, or CreateOS, and it does not mutate billing
state.

## Input boundary

The engine accepts a JSON document with `schema_version: 1` and optional arrays
`billing_records`, `webhook_applications`, `usage_records`, and `usage_syncs`.
All records are observations from caller-provided adapters; the repository does
not claim they came from live Stripe. See `tests/test_reconciliation.py` for
small synthetic test fixtures.

- Billing records contain a transaction ID, currency, provider amount in
  integer minor units, and bridge amount in integer minor units.
- Webhook applications contain a local application ID, provider event ID, and
  transaction ID. Multiple applications for one provider event are reported.
- Usage records are the source observations; usage syncs are downstream
  observations keyed by the same usage ID. Missing syncs and quantity
  differences are reported separately.

Amounts in the JSON boundary must be JSON integers, not floating-point values.
`major_to_minor` is provided for adapters receiving decimal major-unit strings;
it uses `Decimal`, rejects floats and excess precision, and returns an integer.
The initial explicit exponent table includes USD (2) and JPY (0), plus a small
set of common 2- and 3-decimal currencies. Unsupported currencies fail closed;
extend the table from the authoritative provider currency specification before
accepting additional currencies.

## Findings and independent checking

Findings have stable IDs, a code, subject, severity, evidence, and a calculation
rule with the actual operands. The result also includes a SHA-256 digest of the
canonical, sorted input. A separate trusted verifier can validate the digest,
re-run each stated calculation from the original input, and compare the expected
finding set. Result serialization is stable for equivalent inputs regardless
of input record order.

The 100x amount rule identifies a candidate unit mismatch; it does not repair or
rewrite either amount. This catches common major/minor-unit errors for both
zero-decimal JPY and two-decimal USD while preserving the currency exponent in
the evidence. The current engine intentionally makes no claim that every
possible incorrect amount is detectable from these input fields.

The verifier boundary remains a separate trusted component in the established
Rivet architecture. This module is deterministic evidence generation only; it
does not make LLM output trusted, enforce sandbox isolation, or authorize
financial remediation.

## Run

From WSL in the repository:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 scripts/reconcile.py path/to/reconciliation-input.json
```

The runner reads one local JSON file and writes a structured JSON result to
stdout. It performs no network access. No example payment or customer records
are included in this document.

## Verification

The standard-library `unittest` suite covers normal records, duplicate webhook
applications, USD and JPY scaling cases, non-unit discrepancies, dropped and
quantity-mismatched usage, malformed and duplicate inputs, float rejection,
exact decimal conversion, and deterministic output. On 2026-10-17, both
`python3 -m unittest discover -s tests -v` and `python3 -m pytest -q tests`
passed all 11 tests under WSL Python 3.12.3; `compileall` and `git diff --check`
also passed. Running pytest from the repository root additionally collects the
existing CreateOS smoke script, which requires the CreateOS SDK to be installed
in that worktree's Python environment; use the scoped `tests` directory for
this reconciliation suite.
