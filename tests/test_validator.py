import unittest

from tests.test_reconciliation_core import by_invoice, run_wave_03
from agents.validator import validate_hypotheses, validator_node


class HypothesisValidatorTests(unittest.TestCase):
    def test_gateway_fee_worked_example_supports_fee_and_rejects_alternatives(self):
        evidence_bundles = {
            "gateway_fee": [
                {
                    "fact": "gateway_transaction",
                    "value": {
                        "gateway_amount": "10000.00",
                        "gateway_fee": "300.00",
                        "fee_rate": "0.03",
                        "settlement_id": "settlement_991",
                    },
                    "source": "gateway",
                    "source_record_id": "payment_78321",
                    "status": "FOUND",
                },
                {
                    "fact": "bank_settlement",
                    "value": {"bank_amount": "9700.00"},
                    "source": "bank",
                    "source_record_id": "bank_991",
                    "status": "FOUND",
                },
                {
                    "fact": "refund_record",
                    "value": None,
                    "source": "gateway",
                    "source_record_id": "payment_78321",
                    "status": "NOT_FOUND",
                },
            ],
            "refund": [
                {
                    "fact": "refund_record",
                    "value": None,
                    "source": "gateway",
                    "source_record_id": "payment_78321",
                    "status": "NOT_FOUND",
                }
            ],
            "timing_difference": [],
            "manual_adjustment": [],
            "duplicate_or_missing_transaction": [
                {"fact": "related_transactions", "value": {"has_duplicate": False, "has_missing": False}, "status": "NOT_FOUND"}
            ],
            "unknown": [],
        }

        results = {
            result.hypothesis: result
            for result in validate_hypotheses(evidence_bundles)
        }

        fee_result = results["gateway_fee"]
        self.assertEqual(fee_result.conclusion, "SUPPORTED")
        self.assertIn("10000.00 * 0.03 = 300.00", fee_result.calculations)
        self.assertTrue(any("10,000 × 3% = 300" in line for line in fee_result.reasoning))
        self.assertTrue(any("10,000 - 300 = 9,700" in line for line in fee_result.reasoning))

        for hypothesis in (
            "refund",
            "timing_difference",
            "manual_adjustment",
            "duplicate_or_missing_transaction",
            "unknown",
        ):
            with self.subTest(hypothesis=hypothesis):
                self.assertEqual(results[hypothesis].conclusion, "REJECTED")
                self.assertGreater(len(results[hypothesis].reasoning), 0)
                self.assertNotEqual(results[hypothesis].reasoning, ["REJECTED"])

        self.assertIn("no refund record was found", " ".join(results["refund"].reasoning).lower())

    def test_gateway_fee_acceptance_case_from_seed_data(self):
        _, _, split = run_wave_03()
        fee_case = by_invoice(split.exceptions)["INV-1045"]
        canonical = fee_case.canonical_transaction
        evidence_bundles = {
            "gateway_fee": [
                {
                    "fact": "gateway_transaction",
                    "value": {
                        "gateway_amount": canonical["gateway_amount"],
                        "gateway_fee": canonical["gateway_fee"],
                        "fee_rate": str(fee_case.gateway[0].fee_rate),
                    },
                    "source": "gateway",
                    "source_record_id": canonical["gateway_transaction_id"],
                    "status": "FOUND",
                },
                {
                    "fact": "bank_settlement",
                    "value": {"bank_amount": canonical["bank_amount"]},
                    "source": "bank",
                    "source_record_id": canonical["bank_transaction_id"],
                    "status": "FOUND",
                },
                {
                    "fact": "refund_record",
                    "value": None,
                    "source": "gateway",
                    "source_record_id": canonical["gateway_transaction_id"],
                    "status": "NOT_FOUND",
                },
            ],
            "refund": [{"fact": "refund_record", "value": None, "status": "NOT_FOUND"}],
            "unknown": [],
        }

        results = {result.hypothesis: result for result in validate_hypotheses(evidence_bundles)}

        self.assertEqual(results["gateway_fee"].conclusion, "SUPPORTED")
        self.assertIn("10000.00 * 0.03 = 300.00", results["gateway_fee"].calculations)
        self.assertTrue(any("10,000 × 3% = 300" in line for line in results["gateway_fee"].reasoning))
        self.assertEqual(results["refund"].conclusion, "REJECTED")
        self.assertIn("no refund record was found", " ".join(results["refund"].reasoning).lower())

    def test_refund_source_unavailable_is_not_treated_as_rejection(self):
        results = {
            result.hypothesis: result
            for result in validate_hypotheses(
                {
                    "refund": [
                        {"fact": "refund_record", "status": "SOURCE_UNAVAILABLE", "source": "gateway"}
                    ],
                    "gateway_fee": [],
                },
                hypotheses=["refund"],
            )
        }

        self.assertEqual(results["refund"].conclusion, "INSUFFICIENT_EVIDENCE")
        self.assertTrue(results["refund"].missing_evidence)

    def test_validator_node_returns_serializable_validations(self):
        output = validator_node(
            {
                "hypotheses": ["refund"],
                "evidence_bundles": {
                    "refund": [{"fact": "refund_record", "value": None, "status": "NOT_FOUND"}],
                },
            }
        )

        self.assertEqual(output["validations"][0]["hypothesis"], "refund")
        self.assertEqual(output["validations"][0]["conclusion"], "REJECTED")
        self.assertEqual(output["validations"][0]["confidence"], 0.08)


if __name__ == "__main__":
    unittest.main()
