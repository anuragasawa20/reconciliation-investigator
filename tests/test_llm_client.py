import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import HumanMessage, SystemMessage

from agents import llm
from agents.llm import OpenRouterStructuredClient


class LLMClientTests(unittest.TestCase):
    def test_openrouter_client_uses_langchain_chat_openai(self):
        captured = {}

        class FakeBound:
            def invoke(self, messages):
                captured["messages"] = messages
                return SimpleNamespace(content=json.dumps({"ok": True}))

        class FakeChat:
            def __init__(self, **kwargs):
                captured["init"] = kwargs

            def bind(self, **kwargs):
                captured["bind"] = kwargs
                return FakeBound()

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=False):
            with patch("agents.llm._build_chat_openai", side_effect=FakeChat):
                result = OpenRouterStructuredClient(model="openai/gpt-4o-mini").complete_json(
                    task="smoke_test",
                    system_prompt="Return JSON only.",
                    user_payload={"invoice_id": "INV-1"},
                    schema={
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                    },
                )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["init"]["model"], "openai/gpt-4o-mini")
        self.assertEqual(captured["init"]["api_key"], "sk-or-test")
        self.assertEqual(captured["init"]["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(captured["init"]["timeout"], 45.0)
        self.assertEqual(
            captured["init"]["default_headers"]["X-OpenRouter-Title"],
            "Reconciliation Investigator",
        )
        self.assertEqual(
            captured["init"]["extra_body"],
            {"provider": {"require_parameters": True}},
        )
        self.assertEqual(captured["bind"]["response_format"]["type"], "json_schema")
        self.assertTrue(captured["bind"]["response_format"]["json_schema"]["strict"])
        self.assertIsInstance(captured["messages"][0], SystemMessage)
        self.assertIsInstance(captured["messages"][1], HumanMessage)

    def test_complete_json_emits_structured_trace_without_chain_of_thought(self):
        from agents.trace import trace_session

        class FakeBound:
            def invoke(self, messages):
                return SimpleNamespace(content=json.dumps({"ok": True}))

        class FakeChat:
            def __init__(self, **kwargs):
                return None

            def bind(self, **kwargs):
                return FakeBound()

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=False):
            with patch("agents.llm._build_chat_openai", side_effect=FakeChat):
                with trace_session("plan") as events:
                    OpenRouterStructuredClient(model="openai/gpt-4o-mini").complete_json(
                        task="smoke_test",
                        system_prompt="Return JSON only.",
                        user_payload={"invoice_id": "INV-1"},
                        schema={
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                        },
                    )

        llm_events = [event for event in events if event["kind"] == "llm"]
        self.assertEqual(len(llm_events), 1)
        self.assertEqual(llm_events[0]["status"], "COMPLETED")
        self.assertEqual(llm_events[0]["outputs"]["structured"], {"ok": True})
        self.assertNotIn("reasoning", llm_events[0]["outputs"])

    def test_complete_json_emits_running_before_client_construction(self):
        from agents.trace import trace_session

        seen = []

        class FakeBound:
            def invoke(self, messages):
                return SimpleNamespace(content=json.dumps({"ok": True}))

        class FakeChat:
            def __init__(self, **kwargs):
                seen.append("client")

            def bind(self, **kwargs):
                return FakeBound()

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=False):
            with patch("agents.llm._build_chat_openai", side_effect=FakeChat):
                with patch(
                    "agents.llm.emit_live_trace",
                    side_effect=lambda event: seen.append(event.get("status")),
                ):
                    with trace_session("plan"):
                        OpenRouterStructuredClient(model="openai/gpt-4o-mini").complete_json(
                            task="smoke_test",
                            system_prompt="Return JSON only.",
                            user_payload={"invoice_id": "INV-1"},
                            schema={
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {"ok": {"type": "boolean"}},
                                "required": ["ok"],
                            },
                        )

        self.assertEqual(seen[0], "RUNNING")
        self.assertIn("client", seen)
        self.assertLess(seen.index("RUNNING"), seen.index("client"))

    def test_default_client_prefers_openrouter_when_enabled(self):
        with patch.object(llm, "LLM_ENABLED", True), patch.object(llm, "LLM_PROVIDER", "openrouter"):
            self.assertIsInstance(llm.default_llm_client(), OpenRouterStructuredClient)


if __name__ == "__main__":
    unittest.main()
