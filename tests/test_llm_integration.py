import unittest
from decimal import Decimal

from agents.challenger import challenge_hypotheses
from agents.planner import HistoricalCase, plan_investigation
from agents.validator import EvidenceBundle, validate_hypotheses
from tests.test_planner import exception_by_invoice


class FakeStructuredClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class LLMIntegrationTests(unittest.TestCase):
    def test_planner_uses_structured_llm_client_when_provided(self):
        record = exception_by_invoice("INV-1045")
        client = FakeStructuredClient(
            {"live_hypotheses": ["gateway_fee", "unknown_other"]}
        )

        plan = plan_investigation(
            record,
            [HistoricalCase("CASE-OLD-FEE", "gateway_fee")],
            llm_client=client,
        )

        self.assertEqual(plan.live_hypotheses, ["gateway_fee", "unknown_other"])
        self.assertEqual(client.calls[0]["task"], "investigation_plan")

    def test_validator_uses_structured_llm_client_when_provided(self):
        client = FakeStructuredClient(
            {
                "results": [
                    {
                        "hypothesis": "gateway_fee",
                        "evidence_for": [
                            {
                                "fact": "Gateway fee",
                                "value": "300.00",
                                "source": "gateway",
                                "source_record_id": "PAY-1",
                                "retrieval_status": "FOUND",
                            }
                        ],
                        "evidence_against": [],
                        "missing_evidence": [],
                        "reasoning": ["The fee explains the shortfall."],
                        "calculations": ["10000.00 - 300.00 = 9700.00"],
                        "conclusion": "SUPPORTED",
                        "confidence": 0.96,
                    }
                ]
            }
        )

        results = validate_hypotheses(
            [EvidenceBundle("gateway_fee", [{"fact": "Gateway fee", "value": "300.00"}])],
            ["gateway_fee"],
            llm_client=client,
        )

        self.assertEqual(results[0].hypothesis, "gateway_fee")
        self.assertEqual(results[0].conclusion, "SUPPORTED")
        self.assertEqual(results[0].confidence, Decimal("0.96"))
        self.assertEqual(client.calls[0]["task"], "hypothesis_validation")

    def test_challenger_uses_structured_llm_client_when_provided(self):
        record = exception_by_invoice("INV-1045")
        client = FakeStructuredClient(
            {
                "hypothesis": "gateway_fee",
                "checks_performed": [
                    {
                        "name": "fee_matches_shortfall",
                        "passed": True,
                        "detail": "Gateway fee matches the settlement shortfall.",
                    }
                ],
                "contradictory_evidence": [],
                "unresolved_questions": [],
                "challenge_notes": [],
                "adjusted_confidence": 0.95,
                "force_human_review": False,
                "final_explanation": "The gateway fee survived challenge.",
            }
        )

        result = challenge_hypotheses(
            record,
            [
                {
                    "hypothesis": "gateway_fee",
                    "conclusion": "SUPPORTED",
                    "confidence": "0.96",
                    "evidence_for": ["gateway fee"],
                }
            ],
            llm_client=client,
        )

        self.assertEqual(result.hypothesis, "gateway_fee")
        self.assertEqual(result.adjusted_confidence, Decimal("0.95"))
        self.assertEqual(result.final_explanation, "The gateway fee survived challenge.")
        self.assertEqual(client.calls[0]["task"], "hypothesis_challenge")


if __name__ == "__main__":
    unittest.main()

