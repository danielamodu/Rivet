from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rivet.reconciliation import (
    BillingRecord,
    InputValidationError,
    ReconciliationInput,
    UsageRecord,
    UsageSync,
    WebhookApplication,
    load_input,
    major_to_minor,
    parse_input,
    reconcile,
)


class MoneyNormalizationTests(unittest.TestCase):
    def test_usd_major_units_become_integer_cents(self) -> None:
        self.assertEqual(major_to_minor("USD", "12.34"), 1234)

    def test_jpy_has_no_fractional_minor_units(self) -> None:
        self.assertEqual(major_to_minor("JPY", "1234"), 1234)
        self.assertEqual(major_to_minor("JPY", "1234.00"), 1234)
        with self.assertRaises(InputValidationError):
            major_to_minor("JPY", "12.34")

    def test_rejects_floats_and_unsupported_currency(self) -> None:
        with self.assertRaises(InputValidationError):
            major_to_minor("USD", 1.23)  # type: ignore[arg-type]
        with self.assertRaises(InputValidationError):
            major_to_minor("XYZ", "1.00")


class ReconciliationTests(unittest.TestCase):
    def test_normal_records_have_no_findings(self) -> None:
        data = ReconciliationInput(
            billing_records=(BillingRecord("tx_1", "USD", 1234, 1234),),
            webhook_applications=(WebhookApplication("app_1", "evt_1", "tx_1"),),
            usage_records=(UsageRecord("usg_1", "cus_1", 9),),
            usage_syncs=(UsageSync("usg_1", 9),),
        )
        self.assertEqual(reconcile(data).findings, ())

    def test_duplicate_webhook_event_is_reported_with_reproducible_count(self) -> None:
        result = reconcile(
            ReconciliationInput(
                webhook_applications=(
                    WebhookApplication("app_2", "evt_1", "tx_1"),
                    WebhookApplication("app_1", "evt_1", "tx_1"),
                )
            )
        )
        finding = result.findings[0]
        self.assertEqual(finding.code, "DUPLICATE_WEBHOOK_APPLICATION")
        self.assertEqual(finding.calculation["application_count"], 2)
        self.assertEqual(finding.calculation["duplicate_application_count"], 1)
        self.assertEqual(
            [item["application_id"] for item in finding.evidence["applications"]],
            ["app_1", "app_2"],
        )

    def test_usd_and_jpy_factor_100_mismatches_are_reported(self) -> None:
        result = reconcile(
            ReconciliationInput(
                billing_records=(
                    BillingRecord("tx_jpy", "JPY", 5000, 500000),
                    BillingRecord("tx_usd", "USD", 1200, 12),
                    BillingRecord("tx_negative", "JPY", -5000, -500000),
                )
            )
        )
        findings = {finding.subject_id: finding for finding in result.findings}
        self.assertEqual(set(findings), {"tx_jpy", "tx_usd", "tx_negative"})
        self.assertEqual(findings["tx_jpy"].evidence["currency_exponent"], 0)
        self.assertEqual(findings["tx_jpy"].calculation["direction"], "bridge_is_100x_provider")
        self.assertEqual(findings["tx_usd"].evidence["currency_exponent"], 2)
        self.assertEqual(findings["tx_usd"].calculation["direction"], "provider_is_100x_bridge")
        self.assertEqual(findings["tx_negative"].calculation["ratio"], 100)

    def test_non_factor_100_difference_is_not_mislabeled_as_unit_error(self) -> None:
        result = reconcile(
            ReconciliationInput(billing_records=(BillingRecord("tx_1", "USD", 1234, 1200),))
        )
        self.assertEqual(result.findings, ())

    def test_dropped_and_quantity_mismatched_usage_are_reported(self) -> None:
        result = reconcile(
            ReconciliationInput(
                usage_records=(
                    UsageRecord("usg_drop", "cus_1", 5),
                    UsageRecord("usg_mismatch", "cus_1", 7),
                ),
                usage_syncs=(UsageSync("usg_mismatch", 6),),
            )
        )
        findings = {finding.subject_id: finding for finding in result.findings}
        self.assertEqual(findings["usg_drop"].code, "DROPPED_USAGE_SYNC")
        self.assertIsNone(findings["usg_drop"].evidence["downstream_sync"])
        self.assertEqual(findings["usg_mismatch"].code, "USAGE_SYNC_MISMATCH")
        self.assertEqual(findings["usg_mismatch"].calculation["difference"], -1)

    def test_malformed_records_and_duplicate_identifiers_are_rejected(self) -> None:
        with self.assertRaises(InputValidationError):
            parse_input({"schema_version": 1, "billing_records": [{"currency": "USD"}]})
        with self.assertRaises(InputValidationError):
            ReconciliationInput(
                billing_records=(
                    BillingRecord("tx_1", "USD", 1, 1),
                    BillingRecord("tx_1", "USD", 2, 2),
                )
            )
        with self.assertRaises(InputValidationError):
            ReconciliationInput(usage_syncs=(UsageSync("orphan", 1),))

    def test_json_float_literals_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "billing_records": [
                            {
                                "transaction_id": "tx_1",
                                "currency": "USD",
                                "provider_amount_minor": 1.25,
                                "bridge_amount_minor": 125,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(InputValidationError):
                load_input(path)

    def test_output_is_deterministic_and_input_order_independent(self) -> None:
        left = ReconciliationInput(
            billing_records=(
                BillingRecord("tx_2", "JPY", 50, 5000),
                BillingRecord("tx_1", "USD", 100, 10000),
            ),
            webhook_applications=(
                WebhookApplication("app_2", "evt_1", "tx_1"),
                WebhookApplication("app_1", "evt_1", "tx_1"),
            ),
        )
        right = ReconciliationInput(
            billing_records=tuple(reversed(left.billing_records)),
            webhook_applications=tuple(reversed(left.webhook_applications)),
        )
        self.assertEqual(reconcile(left).to_dict(), reconcile(right).to_dict())
        first = reconcile(left).to_dict()
        self.assertEqual(first["summary"]["finding_count"], len(first["findings"]))
        self.assertEqual(len(first["input_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
