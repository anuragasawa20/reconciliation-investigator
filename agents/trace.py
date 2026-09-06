"""User-safe live investigation events for LangGraph streaming.

Events are structured facts: node status, tool results, and parsed LLM JSON.
They must not include hidden chain-of-thought.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_BUFFER: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "agent_trace_buffer", default=None
)
_NODE: ContextVar[str] = ContextVar("agent_trace_node", default="")


def current_node() -> str:
    return _NODE.get() or "graph"


def make_trace_event(
    *,
    event_id: str,
    node: str,
    kind: str,
    name: str,
    status: str,
    summary: str,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    evidence_count: int = 0,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "node": node,
        "kind": kind,
        "name": name,
        "status": status,
        "summary": summary,
        "inputs": inputs or {},
        "outputs": outputs or {},
        "evidence_count": evidence_count,
    }


def _upsert(buffer: list[dict[str, Any]], event: dict[str, Any]) -> None:
    event_id = event.get("event_id")
    if not event_id:
        raise ValueError("Agent trace events require a stable event_id")
    for index, existing in enumerate(buffer):
        if existing.get("event_id") == event_id:
            buffer[index] = event
            return
    buffer.append(event)


def emit_live_trace(event: dict[str, Any]) -> None:
    """Publish one user-safe event to the node buffer and LangGraph custom stream."""

    payload = dict(event)
    buffer = _BUFFER.get()
    if buffer is not None:
        _upsert(buffer, payload)
    if "langgraph" not in sys.modules and "langgraph.config" not in sys.modules:
        return
    try:
        from langgraph.config import get_stream_writer

        get_stream_writer()(payload)
    except Exception:
        return


@contextmanager
def trace_session(node: str) -> Iterator[list[dict[str, Any]]]:
    buffer: list[dict[str, Any]] = []
    buffer_token = _BUFFER.set(buffer)
    node_token = _NODE.set(node)
    try:
        yield buffer
    finally:
        _BUFFER.reset(buffer_token)
        _NODE.reset(node_token)
