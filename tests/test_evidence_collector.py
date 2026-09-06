import ast
from contextlib import ExitStack
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agents import evidence_collector as collector
from agents.planner import HYPOTHESIS_TAXONOMY, InvestigationPlan
from tools import lookups


class EvidenceCollectorTests(unittest.TestCase):
    def test_map_covers_planner_taxonomy(self):
        self.assertEqual(set(collector.HYPOTHESIS_TOOL_MAP), set(HYPOTHESIS_TAXONOMY))
        for names in collector.HYPOTHESIS_TOOL_MAP.values():
            self.assertIsInstance(names, tuple)
            self.assertTrue(all(callable(getattr(lookups, name)) for name in names))

    def test_exact_dispatch_and_untouched_raw_results(self):
        expected_tools = {
            "gateway_fee": ("get_gateway_transaction", "get_fee_configuration", "get_bank_settlement", "get_refund_record"),
            "refund": ("get_gateway_transaction", "get_refund_record", "get_bank_settlement"),
            "timing_difference": ("get_gateway_transaction", "get_bank_settlement"),
            "manual_adjustment": ("get_erp_transaction", "get_related_transactions"),
            "duplicate_or_missing_transaction": ("get_related_transactions",),
            "unknown_other": ("get_related_transactions",),
        }
        calls = []
        results = {}
        def lookup(name):
            def run(**kwargs):
                calls.append((name, kwargs))
                result = {"status": "SOURCE_UNAVAILABLE", "opaque": object()}
                results.setdefault(name, []).append(result)
                return result
            return run
        with ExitStack() as stack:
            for name in {name for names in expected_tools.values() for name in names}:
                stack.enter_context(patch.object(collector.tools, name, side_effect=lookup(name)))
            bundles = collector.collect_evidence(list(expected_tools), "INV-1")
        self.assertEqual(list(bundles), list(expected_tools))
        self.assertEqual(calls, [(name, {"invoice_id": "INV-1"}) for names in expected_tools.values() for name in names])
        for hypothesis, names in expected_tools.items():
            self.assertEqual(tuple(bundles[hypothesis]), names)
            for name in names:
                self.assertIs(bundles[hypothesis][name], results[name].pop(0))

    def test_on_result_receives_start_and_finish_for_each_tool(self):
        observed = []

        def on_result(phase, hypothesis, tool_name, result):
            observed.append((phase, hypothesis, tool_name, result))

        raw = {"status": "NOT_FOUND", "records": []}
        with patch.object(collector.tools, "get_related_transactions", return_value=raw):
            collector.collect_evidence(["unknown_other"], "INV-1", on_result=on_result)

        self.assertEqual(
            observed,
            [
                ("start", "unknown_other", "get_related_transactions", None),
                ("finish", "unknown_other", "get_related_transactions", raw),
            ],
        )

    def test_empty_duplicate_and_invalid_inputs(self):
        with patch.object(collector.tools, "get_related_transactions") as tool:
            self.assertEqual(collector.collect_evidence([], "INV-1"), {})
            tool.assert_not_called()
            collector.collect_evidence(["unknown_other", "unknown_other"], "INV-1")
            tool.assert_called_once_with(invoice_id="INV-1")
            tool.reset_mock()
            for hypotheses, invoice in [(["unknown_other", "invalid"], "INV-1"), (["unknown_other"], " ")]:
                with self.assertRaises(ValueError):
                    collector.collect_evidence(hypotheses, invoice)
            with self.assertRaises(TypeError):
                collector.collect_evidence("gateway_fee", "INV-1")
            tool.assert_not_called()

    def test_wrapper_uses_plan_fields_without_mutating_state(self):
        plan = InvestigationPlan(invoice_id="INV-1", live_hypotheses=["unknown_other"])
        state = {"invoice_id": plan.invoice_id, "live_hypotheses": plan.live_hypotheses, "evidence_bundles": {"stale": {}}, "other": 42}
        original = dict(state)
        raw = {"status": "NOT_FOUND", "records": []}
        with patch.object(collector.tools, "get_related_transactions", return_value=raw):
            update = collector.evidence_collector(state)
        self.assertEqual(state, original)
        self.assertEqual(update, {"evidence_bundles": {"unknown_other": {"get_related_transactions": raw}}})
        self.assertIs(update["evidence_bundles"]["unknown_other"]["get_related_transactions"], raw)

    def test_tool_errors_propagate(self):
        with patch.object(collector.tools, "get_related_transactions", side_effect=OSError("source offline")):
            with self.assertRaisesRegex(OSError, "source offline"):
                collector.collect_evidence(["unknown_other"], "INV-1")

    def test_real_tools_make_zero_api_calls(self):
        # Use actual CSV readers, not mocked evidence tools. Any HTTP or socket
        # attempt raises immediately, and call counts must remain literally zero.
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            fixtures = {
                "erp.csv": [{"transaction_id": "ERP-1", "invoice_id": "INV-1", "amount": "10000.00"}],
                "gateway.csv": [{"transaction_id": "PAY-1", "invoice_id": "INV-1", "amount": "10000.00", "fee": "300.00", "fee_rate": "0.03", "refund_amount": "0.00", "currency": "INR", "settlement_id": "SET-1"}],
                "bank.csv": [{"transaction_id": "BANK-1", "settlement_id": "SET-1", "settled_amount": "9700.00"}],
            }
            for filename, records in fixtures.items():
                with (root / filename).open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(records[0]))
                    writer.writeheader()
                    writer.writerows(records)
            stack.enter_context(patch.object(lookups, "DATA_DIR", root))
            guards = [stack.enter_context(patch(target, side_effect=AssertionError("API call attempted"))) for target in (
                "socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection",
                "http.client.HTTPConnection.request", "urllib.request.urlopen",
            )]
            # Also intercept the HTTP transport used by common model SDKs when installed.
            try:
                import httpx
            except ImportError:
                pass
            else:
                guards.extend(stack.enter_context(patch.object(client, "send", side_effect=AssertionError("API call attempted"))) for client in (httpx.Client, httpx.AsyncClient))
            bundles = collector.collect_evidence(list(HYPOTHESIS_TAXONOMY), "INV-1")
            missing = collector.collect_evidence(["refund"], "INV-MISSING")
            for guard in guards:
                guard.assert_not_called()
            self.assertEqual(bundles["gateway_fee"]["get_fee_configuration"]["records"][0]["fee"], "300.00")
            self.assertEqual(bundles["gateway_fee"]["get_bank_settlement"]["records"][0]["settled_amount"], "9700.00")
            self.assertEqual(bundles["refund"]["get_refund_record"]["status"], "NOT_FOUND")
            self.assertTrue(all(raw["status"] == "NOT_FOUND" for raw in missing["refund"].values()))
            self.assertEqual(bundles, collector.collect_evidence(list(HYPOTHESIS_TAXONOMY), "INV-1"))
            for guard in guards:
                guard.assert_not_called()

    def test_collector_has_no_model_or_network_imports(self):
        tree = ast.parse(Path(collector.__file__).read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertEqual(imports, {"__future__", "collections.abc", "typing", "tools", "schemas"})


if __name__ == "__main__":
    unittest.main()
