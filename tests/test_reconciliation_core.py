import unittest
from decimal import Decimal
from pathlib import Path

from core.exceptions import split_exceptions
from core.loader import load_datasets
from core.matcher import MatchConfig, reconcile
from core.normalizer import normalize_datasets, normalize_invoice_id


ROOT = Path(__file__).resolve().parents[1]


def run_wave_03():
    loaded = load_datasets(ROOT / "data")
    normalized = normalize_datasets(loaded)
    result = reconcile(normalized, MatchConfig(amount_tolerance=Decimal("0.01")))
    return loaded, normalized, split_exceptions(result)


def by_invoice(records):
    return {record.invoice_id: record for record in records}


class ReconciliationCoreTests(unittest.TestCase):
    def test_invoice_id_normalization_variants(self):
        self.assertEqual(normalize_invoice_id("inv1001"), "INV-1001")
        self.assertEqual(normalize_invoice_id("INV 1001"), "INV-1001")
        self.assertEqual(normalize_invoice_id("inv_1001"), "INV-1001")
        self.assertEqual(normalize_invoice_id("INV-1001"), "INV-1001")

    def test_wave_03_loads_and_splits_expected_counts(self):
        loaded, normalized, split = run_wave_03()

        self.assertEqual(loaded.errors, [])
        self.assertEqual(normalized.errors, [])
        self.assertEqual(len(split.matched), 44)
        self.assertEqual(len(split.exceptions), 6)

    def test_rounding_tolerance_case_remains_matched(self):
        _, _, split = run_wave_03()
        matched = by_invoice(split.matched)

        record = matched["INV-1044"]
        self.assertEqual(record.exception_reasons, [])
        self.assertIn("amount_rounding_tolerance", record.matched_by)

    def test_deliberately_hard_cases_land_in_exception_bucket(self):
        _, _, split = run_wave_03()
        exceptions = by_invoice(split.exceptions)

        self.assertEqual(
            set(exceptions),
            {
                "INV-1045",
                "INV-1046",
                "INV-1047",
                "INV-1048",
                "INV-1049",
                "INV-1050",
            },
        )
        self.assertIn("partial_match", exceptions["INV-1045"].exception_reasons)
        self.assertIn("partial_match", exceptions["INV-1046"].exception_reasons)
        self.assertIn("unmatched", exceptions["INV-1047"].exception_reasons)
        self.assertEqual(exceptions["INV-1048"].match_status, "AMBIGUOUS")
        self.assertIn("mismatch", exceptions["INV-1049"].exception_reasons)
        self.assertIn("currency_mismatch", exceptions["INV-1050"].exception_reasons)

    def test_three_way_pairwise_results_are_preserved(self):
        _, _, split = run_wave_03()
        fee_case = by_invoice(split.exceptions)["INV-1045"]
        pair_status = {match.pair: match.status for match in fee_case.pairwise}

        self.assertEqual(
            pair_status,
            {
                "erp_gateway": "MATCHED",
                "erp_bank": "MISMATCH",
                "gateway_bank": "PARTIAL_MATCH",
            },
        )


if __name__ == "__main__":
    unittest.main()
