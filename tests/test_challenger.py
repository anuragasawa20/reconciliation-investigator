import unittest
from decimal import Decimal
from pathlib import Path

from agents.challenger import HypothesisValidation, challenge_hypotheses
from core.exceptions import split_exceptions
from core.loader import load_datasets
from core.matcher import MatchConfig, reconcile
from core.normalizer import normalize_datasets


ROOT = Path(__file__).resolve().parents[1]


def exception_by_invoice(invoice_id):
    loaded = load_datasets(ROOT / "data")
    normalized = normalize_datasets(loaded)
    result = reconcile(normalized, MatchConfig(amount_tolerance=Decimal("0.01")))
    exceptions = {record.invoice_id: record for record in split_exceptions(result).exceptions}
    return exceptions[invoice_id]


class ChallengerTests(unittest.TestCase):
    def test_gateway_fee_confirmation_generates_final_explanation(self):
        record = exception_by_invoice("INV-1045")

        challenge = challenge_hypotheses(
            record,
            [
                HypothesisValidation(
                    hypothesis="gateway_fee",
                    conclusion="SUPPORTED",
                    confidence=Decimal("0.96"),
                    evidence_for=["Gateway fee 300.00", "Bank settlement 9700.00"],
                    calculations=["10000.00 * 0.03 = 300.00", "10000.00 - 300.00 = 9700.00"],
                ),
                HypothesisValidation(
                    hypothesis="refund",
                    conclusion="NOT_SUPPORTED",
                    confidence=Decimal("0.10"),
                    evidence_against=["Gateway refund amount is zero."],
                ),
            ],
        )

        self.assertEqual(challenge.hypothesis, "gateway_fee")
        self.assertFalse(challenge.force_human_review)
        self.assertEqual(challenge.contradictory_evidence, [])
        self.assertGreaterEqual(challenge.adjusted_confidence, Decimal("0.90"))
        self.assertIsNotNone(challenge.final_explanation)
        self.assertIn("10000.00 - 300.00 = 9700.00", challenge.final_explanation or "")
        self.assertIn("refund_not_explaining_difference", {check.name for check in challenge.checks_performed})

    def test_gateway_fee_challenge_lowers_confidence_for_refund_case(self):
        record = exception_by_invoice("INV-1046")

        challenge = challenge_hypotheses(
            record,
            [
                HypothesisValidation(
                    hypothesis="gateway_fee",
                    conclusion="SUPPORTED",
                    confidence=Decimal("0.92"),
                    evidence_for=["Bank shortfall exists."],
                ),
            ],
        )

        self.assertEqual(challenge.hypothesis, "gateway_fee")
        self.assertTrue(challenge.force_human_review)
        self.assertLess(challenge.adjusted_confidence, Decimal("0.70"))
        self.assertIsNone(challenge.final_explanation)
        self.assertTrue(any("refund amount" in item for item in challenge.contradictory_evidence))

    def test_unwarranted_rejection_is_reported_in_challenge_notes(self):
        record = exception_by_invoice("INV-1045")

        challenge = challenge_hypotheses(
            record,
            [
                {
                    "hypothesis": "gateway_fee",
                    "conclusion": "SUPPORTED",
                    "confidence": "0.96",
                    "evidence_for": ["Gateway fee and settlement agree."],
                },
                {
                    "hypothesis": "refund",
                    "conclusion": "NOT_SUPPORTED",
                    "confidence": "0.05",
                    "missing_evidence": ["Refund source was unavailable."],
                    "reasoning": ["No refund evidence was collected."],
                },
            ],
        )

        self.assertEqual(challenge.hypothesis, "gateway_fee")
        self.assertFalse(challenge.force_human_review)
        self.assertTrue(challenge.challenge_notes)
        self.assertIn("rejected refund without contradictory evidence", challenge.challenge_notes[0])


if __name__ == "__main__":
    unittest.main()
