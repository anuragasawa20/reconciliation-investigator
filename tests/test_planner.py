import unittest
from decimal import Decimal
from pathlib import Path

from agents.planner import HistoricalCase, HYPOTHESIS_TAXONOMY, plan_investigation
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


class PlannerTests(unittest.TestCase):
    def test_gateway_fee_case_prunes_duplicate_missing_and_keeps_unknown(self):
        record = exception_by_invoice("INV-1045")

        plan = plan_investigation(
            record,
            [
                HistoricalCase(
                    case_id="CASE-OLD-FEE",
                    hypothesis="gateway_fee",
                    summary="Gross matched and bank settlement was net of payment gateway fee.",
                )
            ],
        )

        self.assertEqual(plan.live_hypotheses[0], "gateway_fee")
        self.assertIn("unknown_other", plan.live_hypotheses)
        self.assertNotIn("duplicate_or_missing_transaction", plan.live_hypotheses)
        self.assertNotIn("refund", plan.live_hypotheses)
        self.assertTrue(set(plan.live_hypotheses).issubset(set(HYPOTHESIS_TAXONOMY)))

    def test_duplicate_or_missing_stays_live_for_ambiguous_source_count(self):
        record = exception_by_invoice("INV-1048")

        plan = plan_investigation(record, [])

        self.assertIn("duplicate_or_missing_transaction", plan.live_hypotheses)
        self.assertEqual(plan.live_hypotheses[-1], "unknown_other")


if __name__ == "__main__":
    unittest.main()

