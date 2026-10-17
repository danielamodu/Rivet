from __future__ import annotations

import copy
import hashlib
import json
import unittest

from rivet.aegis import verify_reconciliation
from rivet.reconciliation import parse_input, reconcile


def sample_input() -> dict[str, object]:
    return {
        "schema_version": 1,
        "billing_records": [
            {
                "transaction_id": "tx_jpy",
                "currency": "JPY",
                "provider_amount_minor": 5000,
                "bridge_amount_minor": 500000,
            },
            {
                "transaction_id": "tx_usd",
                "currency": "USD",
                "provider_amount_minor": 1200,
                "bridge_amount_minor": 12,
            },
        ],
        "webhook_applications": [
            {"application_id": "app_a", "event_id": "evt_repeat", "transaction_id": "tx_usd"},
            {"application_id": "app_b", "event_id": "evt_repeat", "transaction_id": "tx_usd"},
        ],
        "usage_records": [
            {"usage_id": "usg_drop", "customer_id": "cus_a", "quantity": 4},
            {"usage_id": "usg_changed", "customer_id": "cus_a", "quantity": 9},
        ],
        "usage_syncs": [{"usage_id": "usg_changed", "quantity": 8}],
    }


def generated(input_data: dict[str, object]) -> dict[str, object]:
    return reconcile(parse_input(input_data)).to_dict()


def rehash_finding(finding: dict[str, object]) -> None:
    identity = {
        "code": finding["code"],
        "subject_id": finding["subject_id"],
        "evidence": finding["evidence"],
        "calculation": finding["calculation"],
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()[:16]
    finding["finding_id"] = f"{str(finding['code']).lower()}_{digest}"


class AegisVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.input_data = sample_input()
        self.report = generated(self.input_data)

    def test_valid_findings_pass_and_never_authorize_financial_action(self) -> None:
        result = verify_reconciliation(self.input_data, self.report)
        self.assertEqual(result.outcome, "PASS")
        self.assertFalse(result.financial_action_authorized)
        self.assertEqual(result.to_dict()["verification_scope"], "evidence_consistency_only")
        unit = next(
            row for row in result.finding_results if row["classification"] == "candidate_only"
        )
        self.assertEqual(unit["outcome"], "PASS")

    def test_tampered_evidence_is_rejected_even_if_fingerprint_is_rebuilt(self) -> None:
        report = copy.deepcopy(self.report)
        finding = next(row for row in report["findings"] if row["code"] == "DROPPED_USAGE_SYNC")
        finding["evidence"]["source_record"]["quantity"] = 999
        rehash_finding(finding)
        result = verify_reconciliation(self.input_data, report)
        self.assertEqual(result.outcome, "REJECT")
        self.assertIn("EVIDENCE_MISMATCH", {issue.code for issue in result.reasons})

    def test_modified_billing_input_rejects_old_digest_and_findings(self) -> None:
        changed_input = copy.deepcopy(self.input_data)
        changed_input["billing_records"][0]["provider_amount_minor"] = 5100
        changed_input["billing_records"][0]["bridge_amount_minor"] = 510000
        result = verify_reconciliation(changed_input, self.report)
        codes = {issue.code for issue in result.reasons}
        self.assertEqual(result.outcome, "REJECT")
        self.assertIn("INPUT_DIGEST_MISMATCH", codes)
        self.assertIn("CALCULATION_MISMATCH", codes)

    def test_incorrect_currency_exponent_is_rejected_independently(self) -> None:
        report = copy.deepcopy(self.report)
        finding = next(
            row
            for row in report["findings"]
            if row["code"] == "CURRENCY_UNIT_MISMATCH" and row["subject_id"] == "tx_jpy"
        )
        finding["evidence"]["currency_exponent"] = 2
        rehash_finding(finding)
        result = verify_reconciliation(self.input_data, report)
        self.assertIn("EVIDENCE_MISMATCH", {issue.code for issue in result.reasons})

    def test_fabricated_finding_id_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["findings"][0]["finding_id"] = "currency_unit_mismatch_fabricated"
        result = verify_reconciliation(self.input_data, report)
        self.assertIn("FINDING_ID_MISMATCH", {issue.code for issue in result.reasons})

    def test_changed_canonical_input_digest_is_rejected(self) -> None:
        changed_input = copy.deepcopy(self.input_data)
        changed_input["usage_records"][0]["quantity"] = 5
        result = verify_reconciliation(changed_input, self.report)
        self.assertIn("INPUT_DIGEST_MISMATCH", {issue.code for issue in result.reasons})

    def test_reordered_equivalent_input_has_same_canonical_digest(self) -> None:
        reordered = copy.deepcopy(self.input_data)
        reordered["billing_records"].reverse()
        reordered["webhook_applications"].reverse()
        result = verify_reconciliation(reordered, self.report)
        self.assertEqual(result.outcome, "PASS")

    def test_orphan_sync_and_invalid_amount_types_reject_input(self) -> None:
        orphaned = copy.deepcopy(self.input_data)
        orphaned["usage_records"] = []
        result = verify_reconciliation(orphaned, self.report)
        self.assertEqual(result.outcome, "REJECT")
        self.assertEqual(result.reasons[0].code, "INPUT_INVALID")

        extra_field = copy.deepcopy(self.input_data)
        extra_field["unbound_metadata"] = {"source": "ignored by engine"}
        result = verify_reconciliation(extra_field, self.report)
        self.assertEqual(result.outcome, "REJECT")
        self.assertEqual(result.reasons[0].code, "INPUT_INVALID")

        unsupported_currency = copy.deepcopy(self.input_data)
        unsupported_currency["billing_records"][0]["currency"] = "XYZ"
        result = verify_reconciliation(unsupported_currency, self.report)
        self.assertEqual(result.outcome, "REJECT")
        self.assertEqual(result.reasons[0].code, "INPUT_INVALID")

        invalid_amount = copy.deepcopy(self.input_data)
        invalid_amount["billing_records"][0]["bridge_amount_minor"] = 1.5
        result = verify_reconciliation(invalid_amount, self.report)
        self.assertEqual(result.outcome, "REJECT")
        self.assertEqual(result.reasons[0].code, "INPUT_INVALID")

    def test_json_booleans_cannot_masquerade_as_integer_calculations(self) -> None:
        input_data: dict[str, object] = {
            "schema_version": 1,
            "usage_records": [{"usage_id": "u1", "customer_id": "c1", "quantity": 2}],
            "usage_syncs": [{"usage_id": "u1", "quantity": 1}],
        }
        report = generated(input_data)
        finding = report["findings"][0]
        finding["calculation"]["difference"] = True
        rehash_finding(finding)
        report["summary"]["finding_count"] = True
        result = verify_reconciliation(input_data, report)
        codes = {issue.code for issue in result.reasons}
        self.assertIn("CALCULATION_MISMATCH", codes)
        self.assertIn("SUMMARY_MISMATCH", codes)

    def test_false_positive_claim_is_rejected_as_fabricated(self) -> None:
        normal_input: dict[str, object] = {"schema_version": 1}
        report = generated(normal_input)
        report["findings"].append(
            {
                "finding_id": "duplicate_webhook_application_0000000000000000",
                "code": "DUPLICATE_WEBHOOK_APPLICATION",
                "subject_id": "evt_not_present",
                "severity": "high",
                "summary": "A duplicate event was applied.",
                "evidence": {"event_id": "evt_not_present", "applications": []},
                "calculation": {"application_count": 0},
            }
        )
        report["summary"] = {
            "finding_count": 1,
            "counts_by_code": {"DUPLICATE_WEBHOOK_APPLICATION": 1},
        }
        result = verify_reconciliation(normal_input, report)
        codes = {issue.code for issue in result.reasons}
        self.assertIn("FABRICATED_FINDING", codes)
        self.assertIn("SUMMARY_MISMATCH", codes)

    def test_duplicate_and_replayed_findings_are_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["findings"].append(copy.deepcopy(report["findings"][0]))
        result = verify_reconciliation(self.input_data, report)
        self.assertIn("DUPLICATE_OR_REPLAYED_FINDING", {issue.code for issue in result.reasons})

    def test_missing_expected_finding_is_rejected_even_if_summary_is_edited(self) -> None:
        report = copy.deepcopy(self.report)
        report["findings"] = [
            row for row in report["findings"] if row["code"] != "DROPPED_USAGE_SYNC"
        ]
        report["summary"]["finding_count"] -= 1
        result = verify_reconciliation(self.input_data, report)
        codes = {issue.code for issue in result.reasons}
        self.assertIn("MISSING_FINDING", codes)
        self.assertIn("SUMMARY_MISMATCH", codes)

    def test_engine_calculation_change_cannot_be_hidden_by_rehashing_id(self) -> None:
        report = copy.deepcopy(self.report)
        finding = next(
            row for row in report["findings"] if row["code"] == "CURRENCY_UNIT_MISMATCH"
        )
        finding["calculation"]["ratio"] = 99
        rehash_finding(finding)
        result = verify_reconciliation(self.input_data, report)
        self.assertIn("CALCULATION_MISMATCH", {issue.code for issue in result.reasons})
        self.assertEqual(result.outcome, "REJECT")


if __name__ == "__main__":
    unittest.main()
