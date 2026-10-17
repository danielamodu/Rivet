# AEGIS reconciliation evidence verifier

## Scope

The API is rivet.aegis.verify_reconciliation(original_input, generated_result).
It checks a serialized reconciliation result against the original
schema-version-1 JSON input. The verifier uses only the Python standard library.
It does not import or call the reconciliation engine, does not use an LLM, has
no credentials, and performs no network or financial actions.

## Independent checks

- Validates schema version, record types, supported currencies, unique record
  identifiers, usage-sync references, and integer minor-unit/quantity values.
- Rejects unknown input fields. The reconciliation parser currently ignores
  unknown keys while constructing its canonical digest, so AEGIS refuses inputs
  whose extra fields would otherwise be unbound by that digest.
- Independently normalizes and sorts the input rows, then recomputes the
  canonical SHA-256 digest.
- Derives expected duplicate webhook, 100x amount-ratio candidate, dropped
  usage, and quantity-mismatch claims from source input records.
- Recomputes evidence, calculations, currency exponent, finding ID, result
  summary, and required finding coverage. Missing, extra, replayed, fabricated,
  modified, or incomplete findings are rejected.
- A 100x candidate passing means only that the ratio and currency evidence are
  consistent. It is not proof of an actual billing error or permission to act.

Results use explicit PASS or REJECT outcomes and reason codes. The verifier
always returns financial_action_authorized: false; callers must keep human
approval and any eventual remediation outside this verifier.

## Important trust boundary

The verifier establishes consistency with the supplied original input. The
current input schema has no source signatures, Stripe request IDs, database
snapshot identifiers, or provenance attestations. Therefore AEGIS cannot prove
that caller-provided records came from Stripe or that the input itself was not
fabricated. A trusted adapter must authenticate and preserve source records
before invoking this verifier. A PASS is not a financial approval.

The verifier is stateless. It rejects repeated finding claims within one
result and rejects a result when the supplied input has changed. It cannot
distinguish a complete valid result submitted a second time against the exact
same input. Any action consumer that needs cross-run replay protection must
enforce a run identifier or idempotency record outside this verifier.

## Tests

Run from WSL:

    PYTHONPATH=src python3 -m pytest -q tests/test_aegis_verifier.py
    PYTHONPATH=src python3 -m pytest -q tests

The AEGIS tests include valid reports, tampering with and rehashing evidence,
modified input amounts and digests, wrong JPY exponent, fabricated IDs,
malformed/orphan source rows, false-positive claims, missing claims, duplicated
or replayed findings, and changed engine calculations with a recomputed finding
ID. On 2026-10-18, WSL Python 3.12.3 ran 13 AEGIS tests and all 24 repository
tests successfully; compileall and git diff --check also passed.
