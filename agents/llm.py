"""Reusable structured LLM adapter for investigator agents."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from agents.trace import current_node, emit_live_trace, make_trace_event
from config import (
    LLM_ENABLED,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TIMEOUT_SECONDS,
    OPENROUTER_APP_REFERER,
    OPENROUTER_APP_TITLE,
    OPENROUTER_BASE_URL,
)

OPENAI_COMPAT_BASE_URL = "https://api.openai.com/v1"


class StructuredLLMClient(Protocol):
    def complete_json(
        self,
        *,
        task: str,
        system_prompt: str,
        user_payload: Mapping[str, Any],
        schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return model output parsed as a JSON object matching ``schema``."""


class LLMNotConfigured(RuntimeError):
    """Raised when an LLM-backed path is requested without usable credentials."""


def _build_chat_openai(**kwargs: Any) -> Any:
    """Construct LangChain ChatOpenAI. Imported lazily to keep tests off the openai SDK."""

    print("Importing langchain_openai.ChatOpenAI (this is slow if .venv is on iCloud)", flush=True)
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise LLMNotConfigured(
            "Install langchain-openai to enable LLM calls: pip install -U langchain-openai"
        ) from exc
    print("ChatOpenAI imported; constructing client", flush=True)
    return ChatOpenAI(**kwargs)


class OpenAICompatibleStructuredClient:
    """Structured-output client using LangChain ChatOpenAI on an OpenAI-compatible API."""

    def __init__(
        self,
        *,
        model: str = LLM_MODEL,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        default_headers: Mapping[str, str] | None = None,
        require_structured_outputs: bool = False,
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.default_headers = dict(default_headers or {})
        self.require_structured_outputs = require_structured_outputs

    def complete_json(
        self,
        *,
        task: str,
        system_prompt: str,
        user_payload: Mapping[str, Any],
        schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise LLMNotConfigured(f"{self.api_key_env} is required for LLM calls.")

        chat_kwargs: dict[str, Any] = {
            "model": self.model,
            "api_key": api_key,
            "base_url": self.base_url,
            "temperature": 0,
            "timeout": LLM_TIMEOUT_SECONDS,
        }
        if self.default_headers:
            chat_kwargs["default_headers"] = self.default_headers
        if self.require_structured_outputs:
            chat_kwargs["extra_body"] = {"provider": {"require_parameters": True}}

        node = current_node()
        event_id = f"llm:{node}:{task}"
        emit_live_trace(
            make_trace_event(
                event_id=event_id,
                node=node,
                kind="llm",
                name=task,
                status="RUNNING",
                summary=f"Requesting structured JSON for {task.replace('_', ' ')}.",
                inputs={"model": self.model, "provider": LLM_PROVIDER, "task": task},
            )
        )
        try:
            chat = _build_chat_openai(**chat_kwargs).bind(
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": task,
                        "strict": True,
                        "schema": schema,
                    },
                }
            )
            print(
                f"Calling {self.base_url} model={self.model} timeout={LLM_TIMEOUT_SECONDS}s",
                flush=True,
            )
            response = chat.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(
                        content=json.dumps(_jsonable(user_payload), sort_keys=True)
                    ),
                ]
            )
            content = getattr(response, "content", response)
            if not content:
                raise ValueError("LLM response did not include message content.")
            parsed = content if isinstance(content, dict) else _loads_object(content)
        except Exception as exc:
            emit_live_trace(
                make_trace_event(
                    event_id=event_id,
                    node=node,
                    kind="llm",
                    name=task,
                    status="FAILED",
                    summary=f"Structured {task.replace('_', ' ')} call failed.",
                    inputs={"model": self.model, "provider": LLM_PROVIDER, "task": task},
                    outputs={"error_type": type(exc).__name__},
                )
            )
            raise
        emit_live_trace(
            make_trace_event(
                event_id=event_id,
                node=node,
                kind="llm",
                name=task,
                status="COMPLETED",
                summary=f"Received structured JSON for {task.replace('_', ' ')}.",
                inputs={"model": self.model, "provider": LLM_PROVIDER, "task": task},
                outputs={"structured": parsed},
            )
        )
        return parsed


class OpenAIStructuredClient(OpenAICompatibleStructuredClient):
    """Official OpenAI Chat Completions via LangChain ChatOpenAI."""

    def __init__(self, model: str = LLM_MODEL) -> None:
        super().__init__(
            model=model,
            api_key_env="OPENAI_API_KEY",
            base_url=os.getenv("OPENAI_BASE_URL", OPENAI_COMPAT_BASE_URL),
        )


class OpenRouterStructuredClient(OpenAICompatibleStructuredClient):
    """LangChain ChatOpenAI pointed at OpenRouter's OpenAI-compatible endpoint."""

    def __init__(self, model: str = LLM_MODEL) -> None:
        headers = {"X-OpenRouter-Title": OPENROUTER_APP_TITLE}
        if OPENROUTER_APP_REFERER:
            headers["HTTP-Referer"] = OPENROUTER_APP_REFERER
        super().__init__(
            model=model,
            api_key_env="OPENROUTER_API_KEY",
            base_url=OPENROUTER_BASE_URL,
            default_headers=headers,
            require_structured_outputs=True,
        )


def default_llm_client() -> StructuredLLMClient | None:
    if not LLM_ENABLED:
        return None
    if LLM_PROVIDER == "openrouter":
        return OpenRouterStructuredClient()
    if LLM_PROVIDER == "openai":
        return OpenAIStructuredClient()
    raise LLMNotConfigured(f"Unsupported LLM provider: {LLM_PROVIDER}")


def _loads_object(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Structured LLM output must be a JSON object.")
    return parsed


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def jsonable(value: Any) -> Any:
    """Public helper for prompt payload construction."""

    return _jsonable(value)
