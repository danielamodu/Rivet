"""AEGIS verifier for reconciliation evidence.

This module is deliberately separate from the reconciliation engine. It accepts
the raw version-1 input document and the engine's serialized result, then
validates the input, digest, evidence, calculations, IDs, and finding coverage
from the source records. A PASS means evidence consistency only; it never
authorizes a payment, refund, repair, or other financial action.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping


SCHEMA_VERSION = 1

# Deliberately maintained here rather than imported from the engine so the
# trusted checker does not inherit its currency interpretation.
_CURRENCY_EXPONENTS: dict[str, int] = {
    "AUD": 2,
    "BHD": 3,
    "CAD": 2,
    "CHF": 2,
    "CNY": 2,
    "EUR": 2,
    "GBP": 2,
    "INR": 2,
    "JPY": 0,
    "KWD": 3,
    "NZD": 2,
    "OMR": 3,
    "SGD": 2,
    "USD": 2,
}


@dataclass(frozen=True)
class VerificationIssue:
    code: str
    message: str
    finding_index: int | None = None
    finding_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.finding_index is not None:
            result["finding_index"] = self.finding_index
        if self.finding_id is not None:
            result["finding_id"] = self.finding_id
        return result


@dataclass(frozen=True)
class VerificationReport:
    outcome: str
    input_sha256_expected: str | None
    input_sha256_reported: str | None
    finding_results: tuple[dict[str, Any], ...]
    reasons: tuple[VerificationIssue, ...]
    financial_action_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "input_sha256_expected": self.input_sha256_expected,
            "input_sha256_reported": self.input_sha256_reported,
            "finding_results": list(self.finding_results),
            "reasons": [reason.to_dict() for reason in self.reasons],
            "financial_action_authorized": False,
            "verification_scope": "evidence_consistency_only",
        }


class _InvalidInput(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _same_json_value(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool-is-an-int equality quirk."""
    try:
        return _canonical_json(left) == _canonical_json(right)
    except (TypeError, ValueError):
        return False


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _InvalidInput(f"{field} must be a non-empty string")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _InvalidInput(f"{field} must be an integer minor-unit/quantity value")
    return value


def _reject_unknown_fields(row: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(row) - allowed
    if unknown:
        raise _InvalidInput(f"{label} contains unsupported fields: {sorted(unknown)}")


def _rows(document: Mapping[str, Any], field: str) -> list[Mapping[str, Any]]:
    value = document.get(field, [])
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise _InvalidInput(f"{field} must be an array of objects")
    return value


def _unique(rows: list[dict[str, Any]], key: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        value = row[key]
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise _InvalidInput(f"duplicate {key} values: {sorted(duplicates)}")


def _canonical_input(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and independently normalize the documented raw input schema."""
    if not isinstance(raw, Mapping):
        raise _InvalidInput("input document must be a JSON object")
    _reject_unknown_fields(
        raw,
        {
            "schema_version",
            "billing_records",
            "webhook_applications",
            "usage_records",
            "usage_syncs",
        },
        "input document",
    )
    version = raw.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != SCHEMA_VERSION:
        raise _InvalidInput(f"schema_version must be {SCHEMA_VERSION}")

    billing: list[dict[str, Any]] = []
    for row in _rows(raw, "billing_records"):
        _reject_unknown_fields(
            row,
            {"transaction_id", "currency", "provider_amount_minor", "bridge_amount_minor"},
            "billing record",
        )
        transaction_id = _text(row.get("transaction_id"), "transaction_id")
        currency = _text(row.get("currency"), "currency").upper()
        if currency not in _CURRENCY_EXPONENTS:
            raise _InvalidInput(f"unsupported currency: {currency!r}")
        billing.append(
            {
                "transaction_id": transaction_id,
                "currency": currency,
                "provider_amount_minor": _integer(
                    row.get("provider_amount_minor"), "provider_amount_minor"
                ),
                "bridge_amount_minor": _integer(
                    row.get("bridge_amount_minor"), "bridge_amount_minor"
                ),
            }
        )

    applications: list[dict[str, str]] = []
    for row in _rows(raw, "webhook_applications"):
        _reject_unknown_fields(row, {"application_id", "event_id", "transaction_id"}, "webhook application")
        applications.append(
            {
                "application_id": _text(row.get("application_id"), "application_id"),
                "event_id": _text(row.get("event_id"), "event_id"),
                "transaction_id": _text(row.get("transaction_id"), "transaction_id"),
            }
        )

    usage: list[dict[str, Any]] = []
    for row in _rows(raw, "usage_records"):
        _reject_unknown_fields(row, {"usage_id", "customer_id", "quantity"}, "usage record")
        usage.append(
            {
                "usage_id": _text(row.get("usage_id"), "usage_id"),
                "customer_id": _text(row.get("customer_id"), "customer_id"),
                "quantity": _integer(row.get("quantity"), "quantity"),
            }
        )

    syncs: list[dict[str, Any]] = []
    for row in _rows(raw, "usage_syncs"):
        _reject_unknown_fields(row, {"usage_id", "quantity"}, "usage sync")
        syncs.append(
            {
                "usage_id": _text(row.get("usage_id"), "usage_id"),
                "quantity": _integer(row.get("quantity"), "quantity"),
            }
        )

    _unique(billing, "transaction_id")
    _unique(applications, "application_id")
    _unique(usage, "usage_id")
    _unique(syncs, "usage_id")
    known_usage = {row["usage_id"] for row in usage}
    orphan_syncs = sorted(row["usage_id"] for row in syncs if row["usage_id"] not in known_usage)
    if orphan_syncs:
        raise _InvalidInput(f"usage syncs have no source records: {orphan_syncs}")

    return {
        "schema_version": SCHEMA_VERSION,
        "billing_records": sorted(billing, key=lambda row: row["transaction_id"]),
        "webhook_applications": sorted(applications, key=lambda row: row["application_id"]),
        "usage_records": sorted(usage, key=lambda row: row["usage_id"]),
        "usage_syncs": sorted(syncs, key=lambda row: row["usage_id"]),
    }


def _make_finding(
    code: str,
    subject_id: str,
    summary: str,
    evidence: dict[str, Any],
    calculation: dict[str, Any],
) -> dict[str, Any]:
    identity = {
        "code": code,
        "subject_id": subject_id,
        "evidence": evidence,
        "calculation": calculation,
    }
    fingerprint = hashlib.sha256(_canonical_json(identity)).hexdigest()[:16]
    return {
        "finding_id": f"{code.lower()}_{fingerprint}",
        "code": code,
        "subject_id": subject_id,
        "severity": "high",
        "summary": summary,
        "evidence": evidence,
        "calculation": calculation,
    }


def _independent_findings(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive expected claims from inputs, never from supplied calculations."""
    expected: list[dict[str, Any]] = []

    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for application in canonical["webhook_applications"]:
        by_event[application["event_id"]].append(application)
    for event_id, applications in sorted(by_event.items()):
        if len(applications) < 2:
            continue
        ordered = sorted(applications, key=lambda row: row["application_id"])
        count = len(ordered)
        expected.append(
            _make_finding(
                "DUPLICATE_WEBHOOK_APPLICATION",
                event_id,
                f"Webhook event {event_id} was applied {count} times.",
                {"event_id": event_id, "applications": ordered},
                {
                    "application_count": count,
                    "duplicate_application_count": count - 1,
                    "rule": "count(applications with the same event_id) > 1",
                },
            )
        )

    for billing in canonical["billing_records"]:
        provider = billing["provider_amount_minor"]
        bridge = billing["bridge_amount_minor"]
        if provider == bridge or provider == 0 or bridge == 0 or provider * bridge <= 0:
            continue
        provider_abs, bridge_abs = abs(provider), abs(bridge)
        high, low = max(provider_abs, bridge_abs), min(provider_abs, bridge_abs)
        if high != low * 100:
            continue
        direction = (
            "bridge_is_100x_provider" if bridge_abs == high else "provider_is_100x_bridge"
        )
        currency = billing["currency"]
        expected.append(
            _make_finding(
                "CURRENCY_UNIT_MISMATCH",
                billing["transaction_id"],
                f"Bridge and provider amounts differ by a factor of 100 for {currency}.",
                {
                    "transaction_id": billing["transaction_id"],
                    "currency": currency,
                    "provider_amount_minor": provider,
                    "bridge_amount_minor": bridge,
                    "currency_exponent": _CURRENCY_EXPONENTS[currency],
                },
                {
                    "ratio_numerator": high,
                    "ratio_denominator": low,
                    "ratio": 100,
                    "direction": direction,
                    "rule": (
                        "same signs and max(abs(provider), abs(bridge)) == "
                        "100 * min(abs(provider), abs(bridge))"
                    ),
                },
            )
        )

    sync_by_id = {sync["usage_id"]: sync for sync in canonical["usage_syncs"]}
    for source in canonical["usage_records"]:
        sync = sync_by_id.get(source["usage_id"])
        if sync is None:
            expected.append(
                _make_finding(
                    "DROPPED_USAGE_SYNC",
                    source["usage_id"],
                    f"Usage record {source['usage_id']} has no downstream synchronization.",
                    {"source_record": source, "downstream_sync": None},
                    {"rule": "source usage_id absent from synchronized usage_id set"},
                )
            )
        elif sync["quantity"] != source["quantity"]:
            expected.append(
                _make_finding(
                    "USAGE_SYNC_MISMATCH",
                    source["usage_id"],
                    f"Synchronized quantity differs for usage record {source['usage_id']}.",
                    {"source_record": source, "downstream_sync": sync},
                    {
                        "expected_quantity": source["quantity"],
                        "synchronized_quantity": sync["quantity"],
                        "difference": sync["quantity"] - source["quantity"],
                        "rule": "synchronized quantity must equal source quantity",
                    },
                )
            )

    expected.sort(key=lambda row: (row["code"], row["subject_id"], row["finding_id"]))
    return expected


def _summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    codes = sorted({finding["code"] for finding in findings})
    return {
        "finding_count": len(findings),
        "counts_by_code": {
            code: sum(row["code"] == code for row in findings) for code in codes
        },
    }


def verify_reconciliation(
    original_input: Mapping[str, Any], generated_result: Mapping[str, Any]
) -> VerificationReport:
    """Verify a serialized engine result against the original raw input.

    The verifier is read-only and derives all expected findings and arithmetic
    from original_input. Engine-supplied evidence, IDs, digests, and
    calculations are treated as untrusted claims.
    """
    reasons: list[VerificationIssue] = []
    finding_results: list[dict[str, Any]] = []
    expected_digest: str | None = None
    reported_digest: str | None = None

    try:
        canonical = _canonical_input(original_input)
        expected_digest = hashlib.sha256(_canonical_json(canonical)).hexdigest()
        expected_findings = _independent_findings(canonical)
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        reasons.append(VerificationIssue("INPUT_INVALID", str(exc)))
        return VerificationReport(
            "REJECT", None, None, (), tuple(reasons), financial_action_authorized=False
        )

    if not isinstance(generated_result, Mapping):
        reasons.append(VerificationIssue("REPORT_SCHEMA_INVALID", "result must be an object"))
        return VerificationReport(
            "REJECT", expected_digest, None, (), tuple(reasons), financial_action_authorized=False
        )

    required_report_keys = {"schema_version", "input_sha256", "findings", "summary"}
    if set(generated_result) != required_report_keys:
        reasons.append(
            VerificationIssue(
                "REPORT_SCHEMA_INVALID",
                "result must contain exactly schema_version, input_sha256, findings, and summary",
            )
        )
    reported_digest_value = generated_result.get("input_sha256")
    if isinstance(reported_digest_value, str):
        reported_digest = reported_digest_value
    result_version = generated_result.get("schema_version")
    if (
        not isinstance(result_version, int)
        or isinstance(result_version, bool)
        or result_version != SCHEMA_VERSION
    ):
        reasons.append(VerificationIssue("REPORT_SCHEMA_INVALID", "unsupported result schema_version"))
    if reported_digest != expected_digest:
        reasons.append(
            VerificationIssue(
                "INPUT_DIGEST_MISMATCH",
                "reported input digest does not match the independently canonicalized input",
            )
        )

    actual_findings = generated_result.get("findings")
    if not isinstance(actual_findings, list):
        reasons.append(VerificationIssue("FINDINGS_SCHEMA_INVALID", "findings must be an array"))
        actual_findings = []

    expected_by_key = {
        (finding["code"], finding["subject_id"]): finding for finding in expected_findings
    }
    seen_keys: set[tuple[Any, Any]] = set()
    seen_ids: set[str] = set()
    verified_expected_keys: set[tuple[str, str]] = set()

    for index, actual in enumerate(actual_findings):
        row_reasons: list[str] = []
        actual_id: str | None = None
        if not isinstance(actual, Mapping):
            row_reasons.append("FINDING_SCHEMA_INVALID")
            reasons.append(
                VerificationIssue(
                    "FINDING_SCHEMA_INVALID", "finding must be an object", index, None
                )
            )
            finding_results.append(
                {"index": index, "finding_id": None, "outcome": "REJECT", "reasons": row_reasons}
            )
            continue

        id_value = actual.get("finding_id")
        if isinstance(id_value, str):
            actual_id = id_value
        code_value = actual.get("code")
        subject_value = actual.get("subject_id")
        if not isinstance(code_value, str) or not isinstance(subject_value, str):
            row_reasons.append("FINDING_SCHEMA_INVALID")
            reasons.append(
                VerificationIssue(
                    "FINDING_SCHEMA_INVALID",
                    "finding code and subject_id must be strings",
                    index,
                    actual_id,
                )
            )
            key: tuple[str, str] | None = None
        else:
            key = (code_value, subject_value)
        if (key is not None and key in seen_keys) or (
            actual_id is not None and actual_id in seen_ids
        ):
            row_reasons.append("DUPLICATE_OR_REPLAYED_FINDING")
            reasons.append(
                VerificationIssue(
                    "DUPLICATE_OR_REPLAYED_FINDING",
                    "the same finding claim or finding ID appears more than once",
                    index,
                    actual_id,
                )
            )
        if key is not None:
            seen_keys.add(key)
        if actual_id is not None:
            seen_ids.add(actual_id)

        required_finding_keys = {
            "finding_id",
            "code",
            "subject_id",
            "severity",
            "summary",
            "evidence",
            "calculation",
        }
        if set(actual) != required_finding_keys:
            row_reasons.append("FINDING_SCHEMA_INVALID")
            reasons.append(
                VerificationIssue(
                    "FINDING_SCHEMA_INVALID",
                    "finding has missing or unsupported fields",
                    index,
                    actual_id,
                )
            )

        try:
            identity = {
                "code": actual.get("code"),
                "subject_id": actual.get("subject_id"),
                "evidence": actual.get("evidence"),
                "calculation": actual.get("calculation"),
            }
            recomputed_id = (
                f"{str(actual.get('code', '')).lower()}_"
                f"{hashlib.sha256(_canonical_json(identity)).hexdigest()[:16]}"
            )
        except (TypeError, ValueError):
            recomputed_id = None
        if not isinstance(actual_id, str) or actual_id != recomputed_id:
            row_reasons.append("FINDING_ID_MISMATCH")
            reasons.append(
                VerificationIssue(
                    "FINDING_ID_MISMATCH",
                    "finding ID does not match its independently recomputed content fingerprint",
                    index,
                    actual_id,
                )
            )

        expected = expected_by_key.get(key) if key is not None else None
        if expected is None:
            row_reasons.append("FABRICATED_FINDING")
            reasons.append(
                VerificationIssue(
                    "FABRICATED_FINDING",
                    "finding does not correspond to an anomaly independently found in the input",
                    index,
                    actual_id,
                )
            )
        else:
            verified_expected_keys.add(key)
            if not _same_json_value(actual.get("evidence"), expected["evidence"]):
                row_reasons.append("EVIDENCE_MISMATCH")
                reasons.append(
                    VerificationIssue(
                        "EVIDENCE_MISMATCH",
                        "evidence does not exactly reference the source records and currency exponent",
                        index,
                        actual_id,
                    )
                )
            if not _same_json_value(actual.get("calculation"), expected["calculation"]):
                row_reasons.append("CALCULATION_MISMATCH")
                reasons.append(
                    VerificationIssue(
                        "CALCULATION_MISMATCH",
                        "calculation differs from arithmetic independently recomputed from input amounts",
                        index,
                        actual_id,
                    )
                )
            if actual_id != expected["finding_id"] and "FINDING_ID_MISMATCH" not in row_reasons:
                row_reasons.append("FINDING_ID_MISMATCH")
                reasons.append(
                    VerificationIssue(
                        "FINDING_ID_MISMATCH",
                        "finding ID is not the verifier-derived ID for this input claim",
                        index,
                        actual_id,
                    )
                )
            if (
                not _same_json_value(actual.get("severity"), expected["severity"])
                or not _same_json_value(actual.get("summary"), expected["summary"])
            ):
                row_reasons.append("FINDING_METADATA_MISMATCH")
                reasons.append(
                    VerificationIssue(
                        "FINDING_METADATA_MISMATCH",
                        "severity or summary differs from the verifier-derived finding",
                        index,
                        actual_id,
                    )
                )

        finding_results.append(
            {
                "index": index,
                "finding_id": actual_id,
                "outcome": "REJECT" if row_reasons else "PASS",
                "reasons": row_reasons,
                "classification": (
                    "candidate_only"
            if actual.get("code") == "CURRENCY_UNIT_MISMATCH"
                    else "evidence_check"
                ),
            }
        )

    for expected in expected_findings:
        key = (expected["code"], expected["subject_id"])
        if key not in verified_expected_keys:
            reasons.append(
                VerificationIssue(
                    "MISSING_FINDING",
                    "an input-supported finding is absent from the generated result",
                    None,
                    expected["finding_id"],
                )
            )

    if not _same_json_value(generated_result.get("summary"), _summary(expected_findings)):
        reasons.append(
            VerificationIssue(
                "SUMMARY_MISMATCH", "result summary does not match the independently derived findings"
            )
        )

    return VerificationReport(
        outcome="PASS" if not reasons else "REJECT",
        input_sha256_expected=expected_digest,
        input_sha256_reported=reported_digest,
        finding_results=tuple(finding_results),
        reasons=tuple(reasons),
        financial_action_authorized=False,
    )
