import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.types import Command

from graph.reconciliation_graph import _merge_agent_trace, build_app


class ReconciliationGraphTests(unittest.TestCase):
    def setUp(self):
        self.llm_patch = patch("agents.llm.LLM_ENABLED", False)
        self.llm_patch.start()
        self.addCleanup(self.llm_patch.stop)

    def test_missing_bank_settlement_routes_to_wait_without_human_review(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.db"
            app = build_app(db_path)
            config = {"configurable": {"thread_id": "INV-0507"}}

            result = app.invoke({"invoice_id": "INV-0507"}, config=config)

            self.assertEqual(result["root_cause"], "timing_difference")
            self.assertEqual(
                result["recommendation"]["action"], "WAIT_FOR_SETTLEMENT"
            )
            self.assertEqual(result["final_status"], "WAITING_FOR_SETTLEMENT")
            self.assertEqual(app.get_state(config).next, ())
            self.assertNotIn(
                "node:review",
                {event["event_id"] for event in result["agent_trace"]},
            )
            timing = next(
                item for item in result["validations"]
                if item["hypothesis"] == "timing_difference"
            )
            self.assertEqual(timing["conclusion"], "SUPPORTED")
            self.assertTrue(timing["evidence_for"])

    def test_auto_resolve_case_runs_end_to_end_and_writes_state_db(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.db"
            app = build_app(db_path)
            config = {"configurable": {"thread_id": "INV-DEMO-FEE-0001"}}

            result = app.invoke({"invoice_id": "INV-DEMO-FEE-0001"}, config=config)

            self.assertEqual(result["root_cause"], "gateway_fee")
            self.assertEqual(result["final_status"], "AUTO_RESOLVED")
            summary = str(result.get("case_summary") or "").lower()
            self.assertIn("gateway processing fee", summary)
            self.assertIn("shortfall", summary)
            fee_validation = next(
                item for item in result["validations"]
                if item["hypothesis"] == "gateway_fee"
            )
            self.assertTrue(fee_validation["evidence_for"])
            self.assertEqual(app.get_state(config).next, ())
            trace = result["agent_trace"]
            event_ids = [event["event_id"] for event in trace]
            self.assertEqual(len(event_ids), len(set(event_ids)))
            self.assertEqual(
                [event["node"] for event in trace if event["kind"] == "node"],
                [
                    "load_exception",
                    "plan",
                    "collect_evidence",
                    "validate",
                    "challenge",
                    "root_cause",
                    "apply_resolution",
                    "store_case_memory",
                ],
            )
            tool_events = [event for event in trace if event["kind"] == "tool"]
            self.assertTrue(any(event["name"] == "get_fee_configuration" for event in tool_events))
            self.assertTrue(any(event["name"] == "apply_resolution" for event in tool_events))
            self.assertTrue(any(event["name"] == "store_case_memory" for event in tool_events))
            history_events = [
                event for event in tool_events
                if event["name"] == "search_historical_cases"
            ]
            self.assertTrue(history_events)
            self.assertTrue(
                all(
                    event["inputs"]["exclude_invoice_id"]
                    == "INV-DEMO-FEE-0001"
                    for event in history_events
                )
            )
            self.assertNotIn(
                "INV-DEMO-FEE-0001",
                {case["invoice_id"] for case in result["historical_cases"]},
            )
            for event in trace:
                self.assertEqual(
                    set(event),
                    {
                        "event_id",
                        "node",
                        "kind",
                        "name",
                        "status",
                        "summary",
                        "inputs",
                        "outputs",
                        "evidence_count",
                    },
                )
            with sqlite3.connect(db_path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT status, proposed_resolution FROM case_status WHERE invoice_id = ?",
                        ("INV-DEMO-FEE-0001",),
                    ).fetchone(),
                    ("AUTO_RESOLVED", "gateway_fee"),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT pattern, resolution FROM reconciliation_cases WHERE invoice_id = ?",
                        ("INV-DEMO-FEE-0001",),
                    ).fetchone(),
                    ("gateway_fee", "gateway_fee"),
                )

    def test_human_review_case_interrupts_and_resumes_with_command(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.db"
            app = build_app(db_path)
            config = {"configurable": {"thread_id": "INV-DEMO-REFUND-0001"}}

            first = app.invoke({"invoice_id": "INV-DEMO-REFUND-0001"}, config=config)

            self.assertIn("__interrupt__", first)
            self.assertEqual(app.get_state(config).next, ("review",))
            refund_validation = next(
                item for item in first["validations"]
                if item["hypothesis"] == "refund"
            )
            self.assertTrue(refund_validation["evidence_for"])
            waiting_trace = app.get_state(config).values["agent_trace"]
            review_events = [event for event in waiting_trace if event["event_id"] == "node:review"]
            self.assertEqual(len(review_events), 1)
            self.assertEqual(review_events[0]["status"], "WAITING")

            final = app.invoke(Command(resume={"decision": "approve"}), config=config)

            self.assertEqual(final["root_cause"], "refund")
            self.assertEqual(final["final_status"], "HUMAN_APPROVED")
            self.assertEqual(final["review"], {"decision": "approve", "approved": True})
            self.assertEqual(app.get_state(config).next, ())
            review_events = [event for event in final["agent_trace"] if event["event_id"] == "node:review"]
            self.assertEqual(len(review_events), 1)
            self.assertEqual(review_events[0]["status"], "COMPLETED")
            self.assertEqual(review_events[0]["outputs"]["final_status"], "HUMAN_APPROVED")
            self.assertEqual(
                len(final["agent_trace"]),
                len({event["event_id"] for event in final["agent_trace"]}),
            )
            with sqlite3.connect(db_path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT status, proposed_resolution, human_override FROM case_status WHERE invoice_id = ?",
                        ("INV-DEMO-REFUND-0001",),
                    ).fetchone(),
                    ("HUMAN_APPROVED", "refund", "approve"),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT pattern, resolution, human_override FROM reconciliation_cases WHERE invoice_id = ?",
                        ("INV-DEMO-REFUND-0001",),
                    ).fetchone(),
                    ("refund", "refund", "approve"),
                )

    def test_trace_reducer_replaces_duplicate_event_without_reordering(self):
        first = {
            "event_id": "node:review",
            "node": "review",
            "kind": "node",
            "name": "Human review",
            "status": "WAITING",
            "summary": "Waiting for a reviewer.",
            "inputs": {},
            "outputs": {},
            "evidence_count": 0,
        }
        completed = {**first, "status": "COMPLETED", "summary": "Reviewer responded."}
        later = {**first, "event_id": "node:apply_resolution", "node": "apply_resolution"}

        merged = _merge_agent_trace([first, later], [completed])

        self.assertEqual([event["event_id"] for event in merged], ["node:review", "node:apply_resolution"])
        self.assertEqual(merged[0]["status"], "COMPLETED")

    def test_stream_emits_incremental_custom_events_before_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.db"
            app = build_app(db_path)
            config = {"configurable": {"thread_id": "INV-DEMO-FEE-0001"}}
            custom_events = []
            saw_running = False
            for mode, payload in app.stream(
                {"invoice_id": "INV-DEMO-FEE-0001"},
                config=config,
                stream_mode=["custom", "updates"],
            ):
                if mode != "custom" or not isinstance(payload, dict):
                    continue
                custom_events.append(payload)
                if payload.get("status") == "RUNNING":
                    saw_running = True
                if saw_running and len(custom_events) == 1:
                    self.assertEqual(payload["event_id"], "node:load_exception")

            self.assertTrue(saw_running)
            self.assertGreater(len(custom_events), 8)
            statuses_by_id: dict[str, list[str]] = {}
            for event in custom_events:
                statuses_by_id.setdefault(event["event_id"], []).append(event["status"])
            self.assertIn("RUNNING", statuses_by_id["node:load_exception"])
            self.assertEqual(statuses_by_id["node:load_exception"][-1], "COMPLETED")
            self.assertTrue(
                any(event["kind"] == "tool" for event in custom_events)
            )


if __name__ == "__main__":
    unittest.main()
