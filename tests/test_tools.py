import unittest

from tools import (
    get_bank_settlement,
    get_erp_transaction,
    get_fee_configuration,
    get_gateway_transaction,
    get_reconciliation_case,
    get_refund_record,
    get_related_transactions,
    search_historical_cases,
)


class LookupToolTests(unittest.TestCase):
    def test_all_eight_tools_are_isolated_and_source_backed(self):
        invoice = "INV-DEMO-FEE-0001"
        gateway = get_gateway_transaction(invoice_id=invoice)
        self.assertEqual(gateway["status"], "FOUND")
        self.assertEqual(gateway["records"][0]["transaction_id"], "PAY-DEMO-FEE-0001")
        self.assertEqual(get_erp_transaction(invoice_id=invoice)["records"][0]["amount"], "1000.00")
        self.assertEqual(get_fee_configuration(invoice_id=invoice)["records"][0]["fee"], "30.00")
        self.assertEqual(get_bank_settlement(invoice_id=invoice)["records"][0]["settled_amount"], "970.00")
        self.assertEqual(get_refund_record(invoice_id=invoice)["status"], "NOT_FOUND")
        related = get_related_transactions(invoice)
        self.assertEqual(related["status"], "FOUND")
        self.assertEqual(len(related["records"][0]["gateway"]), 1)
        self.assertEqual(search_historical_cases({"pattern": "gateway_fee"}, limit=1)["status"], "FOUND")
        self.assertEqual(get_reconciliation_case(invoice_id=invoice)["records"][0]["resolution"], "AUTO_RESOLVE")

    def test_refund_and_safe_not_found_shapes(self):
        refund = get_refund_record(invoice_id="INV-DEMO-REFUND-0001")
        self.assertEqual(refund["records"][0]["refund_amount"], "284.25")
        missing = get_gateway_transaction(invoice_id="INV-DOES-NOT-EXIST")
        self.assertEqual(missing, {
            "status": "NOT_FOUND", "source": "gateway", "records": [], "count": 0,
            "query": {"transaction_id": None, "invoice_id": "INV-DOES-NOT-EXIST"},
        })


if __name__ == "__main__":
    unittest.main()
