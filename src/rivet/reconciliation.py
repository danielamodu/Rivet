"""Pure, deterministic reconciliation of billing, webhook, and usage inputs.

The module has no network, database, LLM, or filesystem side effects. Monetary
amounts crossing the reconciliation boundary are integer minor units.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1

# Explicitly supported by this initial slice. Unknown currencies fail closed so
# callers cannot silently assume a two-decimal exponent.
CURRENCY_EXPONENTS: dict[str, int] = {
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


class InputValidationError(ValueError):
    """Raised when a reconciliation input is ambiguous or malformed."""


def currency_exponent(currency: str) -> int:
    """Return the configured exponent or reject unsupported currencies."""
    normalized = currency.upper()
    try:
        return CURRENCY_EXPONENTS[normalized]
    except (AttributeError, KeyError) as exc:
        raise InputValidationError(f"unsupported currency: {currency!r}") from exc


def major_to_minor(currency: str, amount: str | Decimal) -> int:
    """Convert a decimal major-unit amount to exact integer minor units.

    Float inputs are deliberately rejected. Values with fractional precision
    beyond the currency exponent are also rejected instead of rounded.
    """
    exponent = currency_exponent(currency)
    if isinstance(amount, float) or isinstance(amount, bool):
        raise InputValidationError("amount must be a decimal string or Decimal")
    if not isinstance(amount, (str, Decimal)):
        raise InputValidationError("amount must be a decimal string or Decimal")
    try:
        decimal_amount = Decimal(amount)
    except (InvalidOperation, ValueError) as exc:
        raise InputValidationError(f"invalid decimal amount: {amount!r}") from exc
    if not decimal_amount.is_finite():
        raise InputValidationError("amount must be finite")

    sign, digits, decimal_exponent = decimal_amount.as_tuple()
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    shift = decimal_exponent + exponent
    if shift >= 0:
        minor_units = coefficient * (10**shift)
    else:
        divisor = 10 ** (-shift)
        minor_units, remainder = divmod(coefficient, divisor)
        if remainder:
            raise InputValidationError(
                f"amount has more than {exponent} decimal places for {currency.upper()}"
            )
    return -minor_units if sign else minor_units


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError(f"{field} must be a non-empty string")
    return value


def _require_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputValidationError(f"{field} must be an integer")
    return value


@dataclass(frozen=True)
class BillingRecord:
    """Amounts from the payment provider and billing bridge, both in minor units."""

    transaction_id: str
    currency: str
    provider_amount_minor: int
    bridge_amount_minor: int

    def __post_init__(self) -> None:
        _require_text(self.transaction_id, "transaction_id")
        normalized = _require_text(self.currency, "currency").upper()
        currency_exponent(normalized)
        object.__setattr__(self, "currency", normalized)
        _require_integer(self.provider_amount_minor, "provider_amount_minor")
        _require_integer(self.bridge_amount_minor, "bridge_amount_minor")

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "currency": self.currency,
            "provider_amount_minor": self.provider_amount_minor,
            "bridge_amount_minor": self.bridge_amount_minor,
        }


@dataclass(frozen=True)
class WebhookApplication:
    """One application of a provider webhook event to the local billing bridge."""

    application_id: str
    event_id: str
    transaction_id: str

    def __post_init__(self) -> None:
        _require_text(self.application_id, "application_id")
        _require_text(self.event_id, "event_id")
        _require_text(self.transaction_id, "transaction_id")

    def to_dict(self) -> dict[str, str]:
        return {
            "application_id": self.application_id,
            "event_id": self.event_id,
            "transaction_id": self.transaction_id,
        }


@dataclass(frozen=True)
class UsageRecord:
    """Authoritative usage observation expected to be synchronized downstream."""

    usage_id: str
    customer_id: str
    quantity: int

    def __post_init__(self) -> None:
        _require_text(self.usage_id, "usage_id")
        _require_text(self.customer_id, "customer_id")
        _require_integer(self.quantity, "quantity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "usage_id": self.usage_id,
            "customer_id": self.customer_id,
            "quantity": self.quantity,
        }


@dataclass(frozen=True)
class UsageSync:
    """A downstream usage synchronization observed by the input adapter."""

    usage_id: str
    quantity: int

    def __post_init__(self) -> None:
        _require_text(self.usage_id, "usage_id")
        _require_integer(self.quantity, "quantity")

    def to_dict(self) -> dict[str, Any]:
        return {"usage_id": self.usage_id, "quantity": self.quantity}


@dataclass(frozen=True)
class ReconciliationInput:
    billing_records: tuple[BillingRecord, ...] = ()
    webhook_applications: tuple[WebhookApplication, ...] = ()
    usage_records: tuple[UsageRecord, ...] = ()
    usage_syncs: tuple[UsageSync, ...] = ()

    def __post_init__(self) -> None:
        _ensure_unique(self.billing_records, "transaction_id")
        _ensure_unique(self.webhook_applications, "application_id")
        _ensure_unique(self.usage_records, "usage_id")
        _ensure_unique(self.usage_syncs, "usage_id")
        source_usage_ids = {record.usage_id for record in self.usage_records}
        orphan_ids = sorted(
            sync.usage_id for sync in self.usage_syncs if sync.usage_id not in source_usage_ids
        )
        if orphan_ids:
            raise InputValidationError(f"usage syncs have no source records: {orphan_ids}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "billing_records": [
                record.to_dict()
                for record in sorted(self.billing_records, key=lambda item: item.transaction_id)
            ],
            "webhook_applications": [
                application.to_dict()
                for application in sorted(
                    self.webhook_applications, key=lambda item: item.application_id
                )
            ],
            "usage_records": [
                record.to_dict()
                for record in sorted(self.usage_records, key=lambda item: item.usage_id)
            ],
            "usage_syncs": [
                sync.to_dict() for sync in sorted(self.usage_syncs, key=lambda item: item.usage_id)
            ],
        }


def _ensure_unique(records: Sequence[Any], field: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for record in records:
        identifier = getattr(record, field)
        if identifier in seen:
            duplicates.add(identifier)
        seen.add(identifier)
    if duplicates:
        raise InputValidationError(f"duplicate {field} values: {sorted(duplicates)}")


@dataclass(frozen=True)
class Finding:
    finding_id: str
    code: str
    subject_id: str
    severity: str
    summary: str
    evidence: dict[str, Any]
    calculation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "code": self.code,
            "subject_id": self.subject_id,
            "severity": self.severity,
            "summary": self.summary,
            "evidence": self.evidence,
            "calculation": self.calculation,
        }


@dataclass(frozen=True)
class ReconciliationResult:
    schema_version: int
    input_sha256: str
    findings: tuple[Finding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "input_sha256": self.input_sha256,
            "findings": [finding.to_dict() for finding in self.findings],
            "summary": {
                "finding_count": len(self.findings),
                "counts_by_code": {
                    code: sum(finding.code == code for finding in self.findings)
                    for code in sorted({finding.code for finding in self.findings})
                },
            },
        }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _finding(
    code: str,
    subject_id: str,
    summary: str,
    evidence: dict[str, Any],
    calculation: dict[str, Any],
) -> Finding:
    identity = {
        "code": code,
        "subject_id": subject_id,
        "evidence": evidence,
        "calculation": calculation,
    }
    digest = hashlib.sha256(_canonical_json(identity)).hexdigest()
    return Finding(
        finding_id=f"{code.lower()}_{digest[:16]}",
        code=code,
        subject_id=subject_id,
        severity="high",
        summary=summary,
        evidence=evidence,
        calculation=calculation,
    )


def reconcile(data: ReconciliationInput) -> ReconciliationResult:
    """Return deterministic findings for the supplied normalized records."""
    findings: list[Finding] = []

    applications_by_event: dict[str, list[WebhookApplication]] = {}
    for application in data.webhook_applications:
        applications_by_event.setdefault(application.event_id, []).append(application)
    for event_id, applications in sorted(applications_by_event.items()):
        if len(applications) > 1:
            ordered = sorted(applications, key=lambda item: item.application_id)
            findings.append(
                _finding(
                    "DUPLICATE_WEBHOOK_APPLICATION",
                    event_id,
                    f"Webhook event {event_id} was applied {len(ordered)} times.",
                    {
                        "event_id": event_id,
                        "applications": [application.to_dict() for application in ordered],
                    },
                    {
                        "application_count": len(ordered),
                        "duplicate_application_count": len(ordered) - 1,
                        "rule": "count(applications with the same event_id) > 1",
                    },
                )
            )

    for record in sorted(data.billing_records, key=lambda item: item.transaction_id):
        provider = record.provider_amount_minor
        bridge = record.bridge_amount_minor
        if provider == bridge or provider == 0 or bridge == 0:
            continue
        provider_magnitude, bridge_magnitude = abs(provider), abs(bridge)
        larger = max(provider_magnitude, bridge_magnitude)
        smaller = min(provider_magnitude, bridge_magnitude)
        # A 100x discrepancy is the signature of a common cents/whole-unit
        # conversion error. It is reported as a candidate with all operands,
        # never silently corrected by this engine.
        if provider * bridge > 0 and larger == smaller * 100:
            direction = (
                "bridge_is_100x_provider"
                if bridge_magnitude == larger
                else "provider_is_100x_bridge"
            )
            exponent = currency_exponent(record.currency)
            findings.append(
                _finding(
                    "CURRENCY_UNIT_MISMATCH",
                    record.transaction_id,
                    f"Bridge and provider amounts differ by a factor of 100 for {record.currency}.",
                    {
                        "transaction_id": record.transaction_id,
                        "currency": record.currency,
                        "provider_amount_minor": provider,
                        "bridge_amount_minor": bridge,
                        "currency_exponent": exponent,
                    },
                    {
                        "ratio_numerator": larger,
                        "ratio_denominator": smaller,
                        "ratio": 100,
                        "direction": direction,
                        "rule": (
                            "same signs and max(abs(provider), abs(bridge)) == "
                            "100 * min(abs(provider), abs(bridge))"
                        ),
                    },
                )
            )

    sync_by_id = {sync.usage_id: sync for sync in data.usage_syncs}
    for source in sorted(data.usage_records, key=lambda item: item.usage_id):
        sync = sync_by_id.get(source.usage_id)
        if sync is None:
            findings.append(
                _finding(
                    "DROPPED_USAGE_SYNC",
                    source.usage_id,
                    f"Usage record {source.usage_id} has no downstream synchronization.",
                    {"source_record": source.to_dict(), "downstream_sync": None},
                    {"rule": "source usage_id absent from synchronized usage_id set"},
                )
            )
        elif sync.quantity != source.quantity:
            findings.append(
                _finding(
                    "USAGE_SYNC_MISMATCH",
                    source.usage_id,
                    f"Synchronized quantity differs for usage record {source.usage_id}.",
                    {"source_record": source.to_dict(), "downstream_sync": sync.to_dict()},
                    {
                        "expected_quantity": source.quantity,
                        "synchronized_quantity": sync.quantity,
                        "difference": sync.quantity - source.quantity,
                        "rule": "synchronized quantity must equal source quantity",
                    },
                )
            )

    findings.sort(key=lambda item: (item.code, item.subject_id, item.finding_id))
    input_sha256 = hashlib.sha256(_canonical_json(data.to_dict())).hexdigest()
    return ReconciliationResult(SCHEMA_VERSION, input_sha256, tuple(findings))


def _record_list(payload: Mapping[str, Any], field: str) -> list[Mapping[str, Any]]:
    value = payload.get(field, [])
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise InputValidationError(f"{field} must be an array of objects")
    return value


def parse_input(payload: Mapping[str, Any]) -> ReconciliationInput:
    """Parse the documented JSON boundary into validated immutable records."""
    if not isinstance(payload, Mapping):
        raise InputValidationError("input document must be a JSON object")
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION or isinstance(version, bool):
        raise InputValidationError(f"schema_version must be {SCHEMA_VERSION}")

    billing = tuple(
        BillingRecord(
            transaction_id=_require_text(row.get("transaction_id"), "transaction_id"),
            currency=_require_text(row.get("currency"), "currency"),
            provider_amount_minor=_require_integer(
                row.get("provider_amount_minor"), "provider_amount_minor"
            ),
            bridge_amount_minor=_require_integer(
                row.get("bridge_amount_minor"), "bridge_amount_minor"
            ),
        )
        for row in _record_list(payload, "billing_records")
    )
    applications = tuple(
        WebhookApplication(
            application_id=_require_text(row.get("application_id"), "application_id"),
            event_id=_require_text(row.get("event_id"), "event_id"),
            transaction_id=_require_text(row.get("transaction_id"), "transaction_id"),
        )
        for row in _record_list(payload, "webhook_applications")
    )
    usage = tuple(
        UsageRecord(
            usage_id=_require_text(row.get("usage_id"), "usage_id"),
            customer_id=_require_text(row.get("customer_id"), "customer_id"),
            quantity=_require_integer(row.get("quantity"), "quantity"),
        )
        for row in _record_list(payload, "usage_records")
    )
    syncs = tuple(
        UsageSync(
            usage_id=_require_text(row.get("usage_id"), "usage_id"),
            quantity=_require_integer(row.get("quantity"), "quantity"),
        )
        for row in _record_list(payload, "usage_syncs")
    )
    return ReconciliationInput(billing, applications, usage, syncs)


def _reject_json_float(_: str) -> Any:
    raise InputValidationError("JSON floating-point numbers are not allowed")


def load_input(path: str | Path) -> ReconciliationInput:
    """Read a JSON input file and reject floating-point numeric literals."""
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            payload = json.load(stream, parse_float=_reject_json_float)
    except json.JSONDecodeError as exc:
        raise InputValidationError(f"invalid JSON at line {exc.lineno}") from exc
    if not isinstance(payload, Mapping):
        raise InputValidationError("input document must be a JSON object")
    return parse_input(payload)
