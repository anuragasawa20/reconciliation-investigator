import unittest

from agents.narrative import compose_investigation_narrative


class NarrativeTests(unittest.TestCase):
    def test_fee_case_states_mismatch_cause_and_action(self):
        text = compose_investigation_narrative(
            {
                "invoice_id": "INV-DEMO-FEE-0001",
                "exception": {
                    "canonical_transaction": {
                        "invoice_id": "INV-DEMO-FEE-0001",
                        "currency": "INR",
                        "expected_amount": "1000.00",
                        "gateway_amount": "1000.00",
                        "gateway_fee": "30.00",
                        "bank_amount": "970.00",
                    }
                },
                "root_cause": "gateway_fee",
                "confidence": "0.96",
                "validations": [
                    {
                        "hypothesis": "gateway_fee",
                        "conclusion": "SUPPORTED",
                        "reasoning": ["The recorded processing fee explains the full settlement shortfall."],
                        "calculations": ["1000.00 * 0.03 = 30.00", "1000.00 - 30.00 = 970.00"],
                    },
                    {
                        "hypothesis": "refund",
                        "conclusion": "REJECTED",
                        "reasoning": ["Refund is rejected because no refund record was found."],
                    },
                ],
                "challenge": {
                    "hypothesis": "gateway_fee",
                    "contradictory_evidence": [],
                    "unresolved_questions": [],
                    "final_explanation": "Gateway fee is confirmed for INV-DEMO-FEE-0001.",
                    "force_human_review": False,
                },
                "recommendation": {
                    "resolution": "gateway_fee",
                    "action": "AUTO_RESOLVE",
                    "reason": "High-confidence source-backed evidence satisfies auto-resolution policy.",
                },
            }
        )

        lower = text.lower()
        self.assertIn("does not fully reconcile", lower)
        self.assertIn("shortfall", lower)
        self.assertIn("gateway processing fee", lower)
        self.assertIn("1000.00 - 30.00 = 970.00", text)
        self.assertIn("refund", lower)
        self.assertIn("auto-resolve", lower)
        self.assertIn("\n\n", text)

    def test_unknown_case_does_not_invent_a_cause(self):
        text = compose_investigation_narrative(
            {
                "exception": {
                    "canonical_transaction": {
                        "invoice_id": "INV-DEMO-UNKNOWN-0001",
                        "currency": "INR",
                        "expected_amount": "1411.00",
                        "gateway_amount": "1411.00",
                        "bank_amount": "1368.67",
                    }
                },
                "root_cause": "unknown",
                "confidence": "0.20",
                "validations": [],
                "challenge": {"contradictory_evidence": [], "unresolved_questions": []},
                "recommendation": {
                    "resolution": "unknown",
                    "action": "HUMAN_REVIEW",
                    "reason": "The leading hypothesis is unknown, so safe abstention is required.",
                },
            }
        )

        lower = text.lower()
        self.assertIn("unknown", lower)
        self.assertIn("human review", lower)
        self.assertNotIn("auto-resolve this as an expected", lower)

    def test_compose_never_raises_on_broken_input(self):
        self.assertIsInstance(compose_investigation_narrative(None), str)
        self.assertIsInstance(compose_investigation_narrative({}), str)
        self.assertIsInstance(compose_investigation_narrative({"exception": object()}), str)


if __name__ == "__main__":
    unittest.main()
