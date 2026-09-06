import unittest
from decimal import Decimal

from agents.recommendation import (
    EvidenceFact,
    HypothesisResult,
    RecommendationAction,
    RecommendationPolicy,
    rank_and_recommend,
    recommend_action,
)


def fact(source, record_id, status="FOUND", name=None):
    return EvidenceFact(
        fact=name or f"{source} evidence",
        source=source,
        source_record_id=record_id,
        retrieval_status=status,
    )


class RecommendationGateTests(unittest.TestCase):
    def setUp(self):
        self.policy = RecommendationPolicy(materiality_ceiling=Decimal("500.00"))

    def assert_action(self, result, expected):
        recommendation = recommend_action(result, self.policy)
        self.assertEqual(recommendation.action, expected)
        return recommendation

    def auto_ready_fee(self, **overrides):
        values = {
            "hypothesis": "gateway_fee",
            "resolution_label": "Expected Gateway Fee",
            "confidence": Decimal("0.96"),
            "evidence_for": (
                fact("erp", "erp_1045"),
                fact("gateway", "payment_78321"),
                fact("bank", "bank_991"),
            ),
            "evidence_against": (),
            "missing_evidence": (),
            "materiality_amount": Decimal("300.00"),
            "required_evidence_present": True,
            "force_human_review": False,
        }
        values.update(overrides)
        return HypothesisResult(**values)

    def test_acceptance_auto_ready_gateway_fee_passes_auto_gate(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(),
            RecommendationAction.AUTO_RESOLVE,
        )

        self.assertFalse(recommendation.human_intervention_required)
        self.assertEqual(recommendation.resolution, "Expected Gateway Fee")
        self.assertIn("source-backed", recommendation.reason)

    def test_acceptance_one_evidence_source_cannot_auto_resolve(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(
                evidence_for=(fact("gateway", "payment_78321"),),
            ),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertTrue(recommendation.human_intervention_required)
        self.assertIn("Fewer than two", recommendation.reason)

    def test_acceptance_materiality_over_ceiling_cannot_auto_resolve(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(materiality_amount=Decimal("500.01")),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertIn("materiality ceiling", recommendation.reason)

    def test_acceptance_unknown_other_leading_abstains_to_human_review(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(
                hypothesis="unknown_other",
                resolution_label="Other Unknown Cause",
            ),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertEqual(recommendation.resolution, "unknown")
        self.assertIn("safe abstention", recommendation.reason)

    def test_missing_evidence_requests_the_exact_missing_item(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(missing_evidence=("bank settlement record",)),
            RecommendationAction.REQUEST_MISSING_EVIDENCE,
        )

        self.assertTrue(recommendation.human_intervention_required)
        self.assertEqual(recommendation.missing_item, "bank settlement record")

    def test_source_unavailable_does_not_count_as_source_backed_evidence(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(
                evidence_for=(
                    fact("gateway", "payment_78321"),
                    fact("bank", "bank_991", status="SOURCE_UNAVAILABLE"),
                ),
            ),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertIn("Fewer than two", recommendation.reason)

    def test_historical_memory_alone_does_not_supply_current_source_records(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(
                evidence_for=(
                    fact("gateway", "payment_78321"),
                    fact("memory", "case_001"),
                ),
            ),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertIn("Fewer than two", recommendation.reason)

    def test_missing_record_id_does_not_count_as_source_backed_evidence(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(
                evidence_for=(
                    fact("gateway", "payment_78321"),
                    fact("bank", None),
                ),
            ),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertIn("Fewer than two", recommendation.reason)

    def test_high_confidence_boundary_is_inclusive_for_auto(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(confidence=Decimal("0.90")),
            RecommendationAction.AUTO_RESOLVE,
        )

        self.assertFalse(recommendation.human_intervention_required)

    def test_below_high_confidence_requires_human_review_even_if_medium(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(confidence=Decimal("0.899")),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertIn("not HIGH", recommendation.reason)

    def test_below_medium_confidence_routes_to_human_review_as_unknown_quality(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(confidence=Decimal("0.699")),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertIn("below the investigation threshold", recommendation.reason)

    def test_materiality_ceiling_boundary_is_inclusive_for_auto(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(materiality_amount=Decimal("500.00")),
            RecommendationAction.AUTO_RESOLVE,
        )

        self.assertFalse(recommendation.human_intervention_required)

    def test_any_contradicting_evidence_blocks_auto_resolution(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(evidence_against=(fact("refund", "refund_991"),)),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertIn("Contradicting evidence", recommendation.reason)

    def test_required_evidence_flag_blocks_auto_resolution(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(required_evidence_present=False),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertIn("Required evidence", recommendation.reason)

    def test_force_human_review_from_challenger_wins_over_auto_ready_facts(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(force_human_review=True),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertIn("forced human review", recommendation.reason)

    def test_non_permitted_hypothesis_cannot_auto_resolve(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(
                hypothesis="refund",
                resolution_label="Refund Explains Shortfall",
            ),
            RecommendationAction.HUMAN_REVIEW,
        )

        self.assertIn("not approved", recommendation.reason)

    def test_timing_difference_waits_for_settlement(self):
        recommendation = self.assert_action(
            self.auto_ready_fee(
                hypothesis="timing_difference",
                resolution_label="Settlement Pending",
            ),
            RecommendationAction.WAIT_FOR_SETTLEMENT,
        )

        self.assertFalse(recommendation.human_intervention_required)

    def test_ranking_uses_leading_hypothesis_before_gate(self):
        lower_auto = self.auto_ready_fee(confidence=Decimal("0.95"))
        higher_unknown = self.auto_ready_fee(
            hypothesis="unknown_other",
            resolution_label="Other Unknown Cause",
            confidence=Decimal("0.96"),
        )

        recommendation = rank_and_recommend(
            (lower_auto, higher_unknown),
            self.policy,
        )

        self.assertEqual(recommendation.action, RecommendationAction.HUMAN_REVIEW)
        self.assertEqual(recommendation.resolution, "unknown")

    def test_empty_ranking_abstains_to_human_review(self):
        recommendation = rank_and_recommend((), self.policy)

        self.assertEqual(recommendation.action, RecommendationAction.HUMAN_REVIEW)
        self.assertEqual(recommendation.resolution, "unknown")

    def test_recommendation_serializes_to_spec_shape(self):
        payload = recommend_action(self.auto_ready_fee(), self.policy).as_dict()

        self.assertEqual(
            payload,
            {
                "resolution": "Expected Gateway Fee",
                "action": "AUTO_RESOLVE",
                "reason": "High-confidence source-backed evidence satisfies auto-resolution policy.",
                "human_intervention_required": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
