import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from data.generate_dataset import generate


class DatasetGeneratorTests(unittest.TestCase):
    def read_rows(self, directory: Path, name: str, key: str = "invoice_id") -> dict[str, dict[str, str]]:
        with (directory / name).open(newline="", encoding="utf-8") as handle:
            return {row[key]: row for row in csv.DictReader(handle)}

    def test_default_dataset_has_volume_and_all_required_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            generate(directory)
            erp = self.read_rows(directory, "erp.csv")
            gateway = self.read_rows(directory, "gateway.csv")
            bank_by_settlement = self.read_rows(directory, "bank.csv", key="settlement_id")
            self.assertEqual(len(erp), 600)
            self.assertEqual(len(gateway), 600)
            self.assertGreaterEqual(len(bank_by_settlement), 500)
            for label in ("gateway_fee", "refund", "timing_difference", "ambiguous", "unknown_other"):
                self.assertIn(label, {row["ground_truth_label"] for row in erp.values()})

            fee = gateway["INV-DEMO-FEE-0001"]
            self.assertEqual((fee["fee"], fee["fee_rate"]), ("30.00", "0.03"))
            self.assertEqual(bank_by_settlement["SET-DEMO-FEE-0001"]["settled_amount"], "970.00")

            refund = gateway["INV-DEMO-REFUND-0001"]
            self.assertEqual(refund["refund_amount"], "284.25")
            self.assertEqual(bank_by_settlement["SET-DEMO-REFUND-0001"]["settled_amount"], "852.75")

            ambiguous = gateway["INV-DEMO-AMBIG-0001"]
            self.assertEqual(ambiguous["fee"], ambiguous["refund_amount"])
            self.assertEqual(bank_by_settlement["SET-DEMO-AMBIG-0001"]["settled_amount"], "1235.78")

            with sqlite3.connect(directory / "historical_cases.db") as connection:
                count = connection.execute("SELECT COUNT(*) FROM reconciliation_cases").fetchone()[0]
                self.assertGreater(count, 0)
                self.assertEqual(
                    connection.execute(
                        "SELECT resolution FROM reconciliation_cases WHERE invoice_id = ?",
                        ("INV-DEMO-FEE-0001",),
                    ).fetchone()[0],
                    "AUTO_RESOLVE",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT resolution FROM reconciliation_cases WHERE invoice_id = ?",
                        ("INV-DEMO-AMBIG-0001",),
                    ).fetchone()[0],
                    "ESCALATE",
                )

    def test_regeneration_replaces_database_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            generate(directory, count=20)
            with sqlite3.connect(directory / "historical_cases.db") as connection:
                first_count = connection.execute("SELECT COUNT(*) FROM reconciliation_cases").fetchone()[0]
            generate(directory)
            with sqlite3.connect(directory / "historical_cases.db") as connection:
                second_count = connection.execute("SELECT COUNT(*) FROM reconciliation_cases").fetchone()[0]
            self.assertEqual(first_count, 4)
            self.assertEqual(second_count, 100)


if __name__ == "__main__":
    unittest.main()
