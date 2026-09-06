"""Streamlit client for the checkpointed reconciliation graph."""

from __future__ import annotations

import html
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_ENV_FILE = PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    for _raw_line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _raw_line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _value = _line.split("=", 1)
        os.environ.setdefault(_key.strip(), _value.strip().strip("\"'"))

from tools.lookups import get_bank_settlement, get_gateway_transaction
from tools.memory_tools import DB_PATH

LIVE_EVENT_PAUSE_SECONDS = 0.45
LLM_EVENT_PAUSE_SECONDS = 0.7
STAGE_ORDER = (
    ("load_exception", "Exception intake", "Load and reconcile", "Intake"),
    ("plan", "Investigation planner", "Generate bounded hypotheses", "Planner"),
    ("collect_evidence", "Evidence collector", "Call source tools", "Evidence"),
    ("validate", "Hypothesis validator", "Test evidence and arithmetic", "Validator"),
    ("challenge", "Challenger", "Try to falsify the leader", "Challenger"),
    ("root_cause", "Decision gate", "Apply control policy", "Decision"),
    ("review", "Human review", "Controlled intervention", "Review"),
    (
        "store_case_memory",
        "Resolution and memory",
        "Write audit and case memory",
        "Memory",
    ),
)
STAGE_ALIASES = {"apply_resolution": "store_case_memory"}


@st.cache_data(show_spinner=False)
def _cases() -> list[dict[str, Any]]:
    cases = []
    gateway_rows = get_gateway_transaction()["records"]
    bank_rows = get_bank_settlement()["records"]
    bank_by_settlement = {
        row["settlement_id"]: row for row in bank_rows if row.get("settlement_id")
    }
    for row in gateway_rows:
        invoice = row["invoice_id"]
        bank = bank_by_settlement.get(row.get("settlement_id"))
        amount = float(row["amount"])
        settled = float(bank["settled_amount"]) if bank else None
        case = {
            "invoice_id": invoice,
            "amount": amount,
            "settled": settled,
            "difference": amount - settled if settled is not None else None,
            "bank_missing": bank is None,
        }
        if case["bank_missing"] or (case["difference"] or 0) > 0:
            cases.append(case)
    return cases


def _status(invoice_id: str) -> str:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS case_status (
                case_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
                status TEXT NOT NULL, proposed_resolution TEXT,
                human_override TEXT, reason TEXT, updated_at TEXT NOT NULL
            )"""
        )
        row = connection.execute(
            "SELECT status FROM case_status WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()
    return row[0] if row else "OPEN"


def _statuses() -> dict[str, str]:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS case_status (
                case_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
                status TEXT NOT NULL, proposed_resolution TEXT,
                human_override TEXT, reason TEXT, updated_at TEXT NOT NULL
            )"""
        )
        rows = connection.execute(
            "SELECT invoice_id, status FROM case_status"
        ).fetchall()
    return dict(rows)


@st.cache_resource
def _graph_app():
    from graph.reconciliation_graph import get_app

    return get_app()


def _run(invoice_id: str, value: Any) -> dict[str, Any]:
    return _graph_app().invoke(
        value, config={"configurable": {"thread_id": invoice_id}}
    )


def _reset_graph_thread(invoice_id: str) -> None:
    checkpointer = getattr(_graph_app(), "checkpointer", None)
    if checkpointer is None or not hasattr(checkpointer, "delete_thread"):
        return
    try:
        checkpointer.delete_thread(invoice_id)
    except Exception:
        return


def _start(invoice_id: str) -> None:
    st.session_state.selected_invoice = invoice_id
    st.session_state.pending_graph_run = {"mode": "start"}
    st.rerun()


def _resume(invoice_id: str, decision: str) -> None:
    st.session_state.selected_invoice = invoice_id
    st.session_state.pending_graph_run = {"mode": "resume", "decision": decision}
    st.rerun()


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def _label(value: Any) -> str:
    text = str(value or "Not available").replace("_", " ").strip()
    return text.title()


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(number, 1.0))


def _money(value: Any, currency: str = "INR") -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "Not available"
    symbol = "₹" if currency == "INR" else f"{currency} "
    return f"{symbol}{amount:,.2f}"


def _pill(text: str, tone: str = "neutral") -> str:
    return f'<span class="trace-pill trace-pill--{tone}">' f"{html.escape(text)}</span>"


def _page_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 2rem; padding-bottom: 4rem; max-width: 1440px;}
        [data-testid="stMetric"] {
            border-top: 1px solid rgba(128, 128, 128, .28);
            padding-top: .85rem;
        }
        .decision-line {font-size: 1.05rem; line-height: 1.55; margin: .3rem 0 1.2rem;}
        .final-reasoning {font-size: 1.05rem; line-height: 1.65; margin: .2rem 0 1.25rem; max-width: 72ch;}
        .final-reasoning p {margin: 0 0 .8rem;}
        .final-reasoning p:last-child {margin-bottom: 0;}
        .path-rail {
            height: 1px; background: rgba(148, 163, 184, .38);
            margin: 18px 6% -19px;
        }
        .path-track {
            display: grid; grid-template-columns: repeat(8, minmax(92px, 1fr));
            gap: 0; min-width: 860px; position: relative;
        }
        .path-track::before {
            content: ""; position: absolute; top: 15px; left: 6%; right: 6%;
            height: 1px; background: rgba(148, 163, 184, .38);
        }
        .path-step {
            display: flex; flex-direction: column; align-items: center;
            text-align: center; padding: 0 .4rem; position: relative; z-index: 1;
        }
        .path-node {
            width: 30px; height: 30px; border-radius: 50%; display: grid;
            place-items: center; color: #0b1118; background: #59d6a9;
            font-size: .78rem; font-weight: 800; border: 2px solid transparent;
        }
        .path-node--running {background: #5ab5f5;}
        .path-node--waiting {background: #f3bf58;}
        .path-node--queued {background: #6b7684; color: #d7dde6;}
        .path-node--failed {background: #ff6b6b;}
        .path-node--selected {border-color: #e8eef6; box-shadow: 0 0 0 3px rgba(90, 181, 245, .45);}
        .path-name {font-size: .78rem; font-weight: 700; line-height: 1.25; margin-top: .45rem;}
        .path-role {font-size: .68rem; line-height: 1.3; opacity: .62; margin-top: .15rem; max-width: 108px;}
        .path-step--queued .path-name, .path-step--queued .path-role {opacity: .48;}
        .path-state {margin-top: .35rem;}
        .path-detail {
            border: 1px solid rgba(128, 128, 128, .28);
            border-radius: 10px; padding: .9rem 1rem 1rem; margin: .35rem 0 1rem;
        }
        .path-detail-head {
            display: flex; gap: .7rem; align-items: flex-start; justify-content: space-between;
            flex-wrap: wrap; margin-bottom: .55rem;
        }
        .path-empty {opacity: .72; font-size: .92rem; margin: .2rem 0 1rem;}
        .live-now {
            border: 1px solid rgba(90, 181, 245, .4);
            padding: .85rem 1rem;
            margin: .75rem 0 1.1rem;
            border-radius: 8px;
        }
        .live-now-label {font-size: .72rem; letter-spacing: .04em; text-transform: uppercase; opacity: .62; margin-bottom: .25rem;}
        .live-now-text {font-size: .98rem; line-height: 1.45;}
        .trace-agent {font-weight: 700; line-height: 1.25;}
        .trace-role {font-size: .78rem; opacity: .64; margin-top: .2rem;}
        .trace-summary {font-size: .92rem; line-height: 1.5; opacity: .9;}
        .trace-pill {
            display: inline-block; padding: .18rem .5rem; border-radius: 999px;
            font-size: .72rem; font-weight: 700; text-transform: uppercase;
            border: 1px solid rgba(128, 128, 128, .35); white-space: nowrap;
        }
        .trace-pill--success {color: #39c98f; border-color: rgba(57, 201, 143, .48);}
        .trace-pill--warning {color: #f3bf58; border-color: rgba(243, 191, 88, .48);}
        .trace-pill--danger {color: #ff6b6b; border-color: rgba(255, 107, 107, .48);}
        .trace-pill--info {color: #5ab5f5; border-color: rgba(90, 181, 245, .48);}
        .call-head {
            display: flex; gap: .55rem; align-items: center; flex-wrap: wrap;
            margin-bottom: .45rem;
        }
        .call-name {font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 700;}
        .call-meta {font-size: .8rem; opacity: .66;}
        .queue-grid {
            display: grid; grid-template-columns: 2.2fr 1fr 1.2fr 1.2fr;
            gap: 1rem; align-items: start; margin-bottom: .75rem;
        }
        .queue-label {font-size: .72rem; opacity: .58; margin-bottom: .25rem;}
        .queue-value {font-size: .92rem; font-weight: 650; overflow-wrap: anywhere;}
        @media (max-width: 760px) {
            .queue-grid {grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); gap: .8rem;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _cell(value: Any) -> Any:
    plain = _plain(value)
    if isinstance(plain, (dict, list)):
        return json.dumps(plain, default=str)
    return plain


def _dataframe_rows(records: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in _plain(records) if isinstance(records, list) else []:
        if not isinstance(record, dict):
            rows.append({"value": str(record)})
            continue
        rows.append({str(key): _cell(value) for key, value in record.items()})
    return rows


def _render_record_tables(records: Any) -> None:
    payload = _plain(records) if records else []
    if (
        isinstance(payload, list)
        and len(payload) == 1
        and isinstance(payload[0], dict)
        and {"erp", "gateway", "bank"} <= set(payload[0].keys())
    ):
        for source in ("erp", "gateway", "bank"):
            st.caption(source.upper())
            rows = _dataframe_rows(payload[0].get(source) or [])
            if rows:
                st.dataframe(rows, hide_index=True, width="stretch")
            else:
                st.info(f"No {source} records were returned.")
        return
    rows = _dataframe_rows(payload)
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")


def _merge_events(
    current: list[dict[str, Any]], updates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = [dict(event) for event in current]
    positions = {
        event.get("event_id"): index
        for index, event in enumerate(merged)
        if event.get("event_id")
    }
    for update in updates:
        event = dict(update)
        event_id = event.get("event_id")
        if not event_id:
            continue
        if event_id in positions:
            merged[positions[event_id]] = event
        else:
            positions[event_id] = len(merged)
            merged.append(event)
    return merged


def _canonical_stage(node: str) -> str:
    return STAGE_ALIASES.get(str(node or ""), str(node or ""))


def _latest_event(events: list[dict[str, Any]], node: str) -> dict[str, Any] | None:
    match = None
    for event in events:
        if event.get("kind") == "node" and _canonical_stage(event.get("node")) == node:
            match = event
    return match


def _events_for_stage(events: list[dict[str, Any]], node: str) -> list[dict[str, Any]]:
    return [event for event in events if _canonical_stage(event.get("node")) == node]


def _stage_tone(state: str) -> tuple[str, str]:
    mapping = {
        "Complete": ("success", ""),
        "Running": ("info", "running"),
        "Waiting": ("warning", "waiting"),
        "Failed": ("danger", "failed"),
        "Queued": ("neutral", "queued"),
    }
    return mapping.get(state, ("neutral", "queued"))


def _stage_rows(values: dict[str, Any], paused: bool) -> list[dict[str, Any]]:
    exception = values.get("exception")
    hypotheses = values.get("live_hypotheses", [])
    bundles = values.get("evidence_bundles", {})
    validations = values.get("validations", [])
    challenge = values.get("challenge", {})
    recommendation = values.get("recommendation", {})
    review = values.get("review", {})
    completed = bool(values.get("completed_at"))
    tool_calls = sum(len(tools) for tools in bundles.values())
    found_calls = sum(
        1
        for tools in bundles.values()
        for result in tools.values()
        if result.get("status") == "FOUND"
    )
    summaries = {
        "load_exception": (
            f"Matched ERP, gateway and bank records; isolated {_label(getattr(exception, 'match_status', 'exception')).lower()}."
            if exception
            else "Waiting for a reconciliation exception."
        ),
        "plan": (
            f"Opened {len(hypotheses)} controlled hypotheses in priority order."
            if hypotheses
            else "Waiting for the exception profile."
        ),
        "collect_evidence": (
            f"Ran {tool_calls} source lookups; {found_calls} returned records and {tool_calls - found_calls} returned explicit gaps."
            if bundles
            else "Waiting for the investigation plan."
        ),
        "validate": (
            f"Compared {len(validations)} explanations against source facts and deterministic calculations."
            if validations
            else "Waiting for source evidence."
        ),
        "challenge": (
            f"Performed {len(challenge.get('checks_performed', []))} counter-checks; found {len(challenge.get('contradictory_evidence', []))} contradictions."
            if challenge
            else "Waiting for validated hypotheses."
        ),
        "root_cause": (
            f"Recommended {_label(recommendation.get('resolution')).lower()} with {_label(recommendation.get('action')).lower()}."
            if recommendation
            else "Waiting for the challenger."
        ),
        "review": (
            f"Decision recorded: {_label(review.get('decision')).lower()}."
            if review
            else (
                "Approval is required before any financial action."
                if paused
                else "No human intervention was required."
            )
        ),
        "store_case_memory": (
            f"Closed as {_label(values.get('final_status')).lower()} and stored an auditable case pattern."
            if completed
            else "Waiting for the final decision."
        ),
    }
    done_flags = {
        "load_exception": bool(exception),
        "plan": bool(hypotheses),
        "collect_evidence": bool(bundles),
        "validate": bool(validations),
        "challenge": bool(challenge),
        "root_cause": bool(recommendation),
        "review": bool(review),
        "store_case_memory": completed,
    }
    stages = []
    for index, (node, agent, role, short) in enumerate(STAGE_ORDER, start=1):
        waiting = node == "review" and paused and not review
        done = done_flags[node]
        state = "Complete" if done else "Waiting" if waiting else "Queued"
        tone, node_mod = _stage_tone(state)
        stages.append(
            {
                "node": node,
                "index": index,
                "agent": agent,
                "role": role,
                "short": short,
                "state": state,
                "tone": tone,
                "node_mod": node_mod,
                "summary": summaries[node],
            }
        )
    return stages


def _stages_from_events(
    events: list[dict[str, Any]], paused: bool
) -> list[dict[str, Any]]:
    stages = []
    reached_active = False
    for index, (node, agent, role, short) in enumerate(STAGE_ORDER, start=1):
        event = _latest_event(events, node)
        status = str(event.get("status", "")).upper() if event else ""
        if status == "COMPLETED":
            state = "Complete"
            summary = str(event.get("summary") or "")
        elif status == "RUNNING":
            state = "Running"
            summary = str(event.get("summary") or "")
            reached_active = True
        elif status in {"WAITING", "PAUSED"} or (
            node == "review" and paused and not status
        ):
            state = "Waiting"
            summary = str(
                (event or {}).get("summary")
                or "Approval is required before any financial action."
            )
            reached_active = True
        elif status == "FAILED":
            state = "Failed"
            summary = str(event.get("summary") or "")
            reached_active = True
        else:
            state = "Queued"
            summary = "Waiting for the previous stage to finish."
            if reached_active is False and event is None and index == 1:
                summary = "Waiting to start."
        tone, node_mod = _stage_tone(state)
        stages.append(
            {
                "node": node,
                "index": index,
                "agent": agent,
                "role": role,
                "short": short,
                "state": state,
                "tone": tone,
                "node_mod": node_mod,
                "summary": summary,
            }
        )
    return stages


def _build_stages(values: dict[str, Any], paused: bool) -> list[dict[str, Any]]:
    events = values.get("agent_trace") or values.get("process_trace") or []
    if events:
        return _stages_from_events(events, paused)
    return _stage_rows(values, paused)


def _default_stage_node(stages: list[dict[str, Any]]) -> str:
    for stage in stages:
        if stage["state"] in {"Running", "Waiting", "Failed"}:
            return stage["node"]
    for stage in stages:
        if stage["node"] == "root_cause" and stage["state"] == "Complete":
            return stage["node"]
    completed = [stage for stage in stages if stage["state"] == "Complete"]
    return completed[-1]["node"] if completed else stages[0]["node"]


def _path_step_html(stage: dict[str, Any], selected: str | None) -> str:
    node_class = f" path-node--{stage['node_mod']}" if stage["node_mod"] else ""
    if selected == stage["node"]:
        node_class += " path-node--selected"
    queued_class = " path-step--queued" if stage["state"] == "Queued" else ""
    return (
        f'<div class="path-step{queued_class}">'
        f'<div class="path-node{node_class}">{stage["index"]}</div>'
        f'<div class="path-name">{html.escape(stage["short"])}</div>'
        f'<div class="path-role">{html.escape(stage["agent"])}</div>'
        f'<div class="path-state">{_pill(stage["state"], stage["tone"])}</div>'
        "</div>"
    )


def _render_path_track(stages: list[dict[str, Any]], selected: str | None) -> None:
    cells = [_path_step_html(stage, selected) for stage in stages]
    st.markdown(
        '<div class="path-scroll"><div class="path-track">'
        + "".join(cells)
        + "</div></div>",
        unsafe_allow_html=True,
    )


def _render_event_markdown(events: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    for event in events:
        kind = str(event.get("kind") or "step")
        name = str(event.get("name") or "")
        status = str(event.get("status") or "")
        summary = str(event.get("summary") or "")
        lines.append(f"- `{kind}` **{name}** · {status} — {summary}")
        structured = (event.get("outputs") or {}).get("structured")
        if kind == "llm" and structured:
            payload = json.dumps(_plain(structured), indent=2, default=str)
            lines.append("```json")
            lines.append(payload)
            lines.append("```")
    if lines:
        st.markdown("\n".join(lines))


def _render_stage_results(
    stage: dict[str, Any],
    values: dict[str, Any],
    invoice: str,
    *,
    widgets: bool,
) -> None:
    events = _events_for_stage(
        values.get("agent_trace") or values.get("process_trace") or [], stage["node"]
    )
    if not widgets:
        _render_event_markdown(events)
        return
    node = stage["node"]
    if node == "load_exception":
        _render_exception(values)
    elif node == "plan":
        hypotheses = values.get("live_hypotheses") or []
        if hypotheses:
            st.write(
                "Bounded hypotheses: " + ", ".join(_label(item) for item in hypotheses)
            )
        else:
            st.info("The planner has not published a hypothesis set yet.")
    elif node == "validate":
        _render_validation(values)
    elif node == "challenge":
        _render_challenge(values)
    elif node == "root_cause":
        _render_decision(values)
    elif node == "review":
        review = values.get("review") or {}
        if review:
            st.info(f"Human decision recorded: {_label(review.get('decision'))}")
        elif stage["state"] == "Waiting":
            st.warning("Approval is required before any financial action.")
        else:
            st.info("No human intervention was required for this case.")
    elif node == "store_case_memory":
        _render_audit(values)
    llm_events = [event for event in events if event.get("kind") == "llm"]
    tool_events = [event for event in events if event.get("kind") == "tool"]
    if llm_events:
        st.markdown("**Structured model outputs**")
        for event in llm_events:
            _render_llm_event(
                event, expanded=str(event.get("status", "")).upper() == "RUNNING"
            )
    if tool_events:
        st.markdown("**Tool calls**")
        for event in tool_events:
            _render_tool_call_event(
                event,
                invoice,
                expanded=str(event.get("status", "")).upper() == "RUNNING",
            )
    elif node == "collect_evidence" and not (values.get("agent_trace") or []):
        _render_tool_calls(values, invoice)


def _render_timeline(
    values: dict[str, Any],
    paused: bool,
    *,
    invoice: str = "",
    interactive: bool = True,
) -> None:
    events = values.get("agent_trace") or values.get("process_trace") or []
    stages = _build_stages(values, paused)
    running = next(
        (
            event
            for event in reversed(events)
            if str(event.get("status", "")).upper() == "RUNNING"
        ),
        None,
    )
    if running:
        st.markdown(
            '<div class="live-now">'
            '<div class="live-now-label">Now happening</div>'
            f'<div class="live-now-text">{html.escape(str(running.get("summary") or running.get("name")))}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
    state_key = f"path_stage_{invoice or 'live'}"
    if interactive:
        if state_key not in st.session_state:
            st.session_state[state_key] = _default_stage_node(stages)
        selected = st.session_state.get(state_key)
    else:
        selected = _default_stage_node(stages)
    if interactive:
        st.caption("Open a stage to inspect its results. Hide collapses the panel.")
        st.markdown('<div class="path-rail"></div>', unsafe_allow_html=True)
        columns = st.columns(len(stages), gap="small")
        for column, stage in zip(columns, stages):
            selected_here = selected == stage["node"]
            with column:
                st.markdown(_path_step_html(stage, selected), unsafe_allow_html=True)
                if st.button(
                    "Hide" if selected_here else "Open",
                    key=f"{state_key}-{stage['node']}",
                    type="secondary",
                    width="stretch",
                    help=f"{stage['agent']} · {stage['role']}",
                ):
                    st.session_state[state_key] = (
                        None if selected_here else stage["node"]
                    )
                    st.rerun()
        selected = st.session_state.get(state_key)
    else:
        _render_path_track(stages, selected)
    chosen = next((stage for stage in stages if stage["node"] == selected), None)
    if chosen is None:
        st.markdown(
            '<div class="path-empty">Stage results are hidden. Select a stage on the path to inspect it.</div>',
            unsafe_allow_html=True,
        )
        return
    st.markdown(
        '<div class="path-detail">'
        '<div class="path-detail-head">'
        f'<div><div class="trace-agent">{html.escape(chosen["agent"])}</div>'
        f'<div class="trace-role">{html.escape(chosen["role"])}</div></div>'
        f'{_pill(chosen["state"], chosen["tone"])}'
        "</div>"
        f'<div class="trace-summary">{html.escape(chosen["summary"])}</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    _render_stage_results(chosen, values, invoice, widgets=interactive)


def _render_backend_trace(events: list[dict[str, Any]]) -> None:
    _render_timeline({"agent_trace": events}, paused=False, interactive=False)


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _merge_events([], events)


def _render_tool_call_event(
    event: dict[str, Any], invoice: str, expanded: bool
) -> None:
    status = str(
        event.get("status")
        or event.get("outputs", {}).get("retrieval_status")
        or "UNKNOWN"
    ).upper()
    outputs = event.get("outputs") or {}
    count = int(outputs.get("record_count") or event.get("evidence_count") or 0)
    source = outputs.get("source", "unknown")
    name = str(event.get("name") or "tool")
    icon = (
        "✓"
        if status == "FOUND"
        else "…" if status == "RUNNING" else "−" if status == "NOT_FOUND" else "!"
    )
    pattern = str(
        (event.get("inputs") or {}).get("pattern")
        or (event.get("inputs") or {}).get("hypothesis")
        or ""
    )
    title = f"{icon} {name} · {status} · {count} record(s)"
    if pattern:
        title += f" · {pattern}"
    with st.expander(title, expanded=expanded):
        st.markdown(
            '<div class="call-head">'
            f'<span class="call-name">{html.escape(name)}</span>'
            f'{_pill(status, "success" if status in {"FOUND", "COMPLETED"} else "info" if status == "RUNNING" else "warning")}'
            f'<span class="call-meta">source: {html.escape(str(source))}</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        inputs = event.get("inputs") or {}
        argument = inputs.get("invoice_id") or invoice
        hypothesis = inputs.get("hypothesis")
        caption = f"Arguments · invoice_id={argument}"
        if hypothesis:
            caption += f" · hypothesis={hypothesis}"
        st.caption(caption)
        if event.get("summary"):
            st.write(event["summary"])
        records = outputs.get("records") or []
        if records:
            _render_record_tables(records)
        elif status == "RUNNING":
            st.info("Waiting for the tool result.")
        else:
            st.info(
                "The source returned no matching record. The gap is retained for validation."
            )


def _render_llm_event(event: dict[str, Any], expanded: bool) -> None:
    status = str(event.get("status", "UNKNOWN")).upper()
    name = str(event.get("name") or "structured_output")
    node = str(event.get("node") or "")
    title = f"Structured LLM · {name} · {status}"
    if node:
        title += f" · {node}"
    with st.expander(title, expanded=expanded):
        st.caption(
            "Parsed JSON only. Hidden chain-of-thought is never displayed, and this output is not a source of financial truth."
        )
        inputs = event.get("inputs") or {}
        st.write(
            f"Model `{inputs.get('model', 'unknown')}` · task `{name}` · "
            f"{event.get('summary', '')}"
        )
        outputs = event.get("outputs") or {}
        structured = outputs.get("structured", outputs)
        if status == "RUNNING":
            st.info("Waiting for the structured response.")
        elif status == "FAILED":
            st.error(
                f"The structured call failed: {outputs.get('error_type', 'error')}"
            )
        if structured:
            st.json(_plain(structured))


def _render_stream_log(events: list[dict[str, Any]]) -> None:
    """Key-free live log. Streamlit expanders inside st.empty() collide on redraw."""
    _render_timeline({"agent_trace": events}, paused=False, interactive=False)
    lines = ["#### Live steps"]
    for event in _dedupe_events(events):
        kind = str(event.get("kind") or "step")
        name = str(event.get("name") or "")
        status = str(event.get("status") or "")
        summary = str(event.get("summary") or "")
        lines.append(f"- `{kind}` **{name}** · {status} — {summary}")
        structured = (event.get("outputs") or {}).get("structured")
        if kind == "llm" and structured:
            payload = json.dumps(_plain(structured), indent=2, default=str)
            lines.append("```json")
            lines.append(payload)
            lines.append("```")
    st.markdown("\n".join(lines))


def _render_live_activity(events: list[dict[str, Any]], invoice: str) -> None:
    events = _dedupe_events(events)
    llm_events = [event for event in events if event.get("kind") == "llm"]
    tool_events = [event for event in events if event.get("kind") == "tool"]
    latest_id = events[-1].get("event_id") if events else None
    if llm_events:
        st.markdown("#### Structured model outputs")
        for event in llm_events:
            _render_llm_event(
                event,
                expanded=event.get("event_id") == latest_id
                or event.get("status") == "RUNNING",
            )
    st.markdown("#### Tool calls")
    if not tool_events:
        st.info("No source tools have been called yet.")
        return
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in tool_events:
        key = str(
            (event.get("inputs") or {}).get("hypothesis")
            or event.get("node")
            or "tools"
        )
        grouped.setdefault(key, []).append(event)
    for group, group_events in grouped.items():
        if group not in {
            "plan",
            "collect_evidence",
            "apply_resolution",
            "store_case_memory",
            "tools",
        }:
            st.markdown(f"**{_label(group)} hypothesis**")
        elif group == "plan":
            st.markdown("**Historical memory**")
        for event in group_events:
            _render_tool_call_event(
                event,
                invoice,
                expanded=event.get("event_id") == latest_id
                or event.get("status") == "RUNNING",
            )


def _render_tool_calls(values: dict[str, Any], invoice: str) -> None:
    events = [
        event
        for event in values.get("agent_trace") or []
        if event.get("kind") in {"tool", "llm"}
    ]
    if events:
        _render_live_activity(events, invoice)
        return
    bundles = values.get("evidence_bundles", {})
    historical = values.get("historical_cases", [])
    st.markdown("#### Tool calls")
    with st.expander(
        f"search_historical_cases · {'FOUND' if historical else 'NOT_FOUND'} · {len(historical)} record(s)",
        expanded=False,
    ):
        st.caption(
            f"Called by Investigation planner · active invoice excluded: {invoice}"
        )
        st.write(
            "Searches same-pattern resolved precedents without using the active case as its own evidence."
        )
        if historical:
            st.dataframe(_dataframe_rows(historical), hide_index=True, width="stretch")
        else:
            st.info(
                "No matching historical precedent was returned. This is evidence, not an error."
            )
    if not bundles:
        st.info("No evidence tool calls have run yet.")
        return
    for hypothesis, tools in bundles.items():
        st.markdown(f"**{_label(hypothesis)} hypothesis**")
        for tool_name, result in tools.items():
            status = result.get("status", "UNKNOWN")
            count = int(result.get("count", len(result.get("records", []))) or 0)
            source = result.get("source", "unknown")
            icon = "✓" if status == "FOUND" else "−" if status == "NOT_FOUND" else "!"
            with st.expander(
                f"{icon} {tool_name} · {status} · {count} record(s)", expanded=False
            ):
                st.markdown(
                    '<div class="call-head">'
                    f'<span class="call-name">{html.escape(tool_name)}</span>'
                    f'{_pill(status, "success" if status == "FOUND" else "warning")}'
                    f'<span class="call-meta">source: {html.escape(str(source))}</span>'
                    "</div>",
                    unsafe_allow_html=True,
                )
                st.caption(f"Arguments · invoice_id={invoice}")
                records = result.get("records", [])
                if records:
                    _render_record_tables(records)
                else:
                    st.info(
                        "The source returned no matching record. The gap is retained for validation."
                    )


def _render_exception(values: dict[str, Any]) -> None:
    exception = values.get("exception")
    if not exception:
        st.info("The exception profile is not available yet.")
        return
    canonical = exception.canonical_transaction
    cols = st.columns(4)
    cols[0].metric(
        "ERP expected",
        _money(canonical.get("expected_amount"), canonical.get("currency") or "INR"),
    )
    cols[1].metric(
        "Gateway gross",
        _money(canonical.get("gateway_amount"), canonical.get("currency") or "INR"),
    )
    cols[2].metric(
        "Gateway fee",
        _money(canonical.get("gateway_fee"), canonical.get("currency") or "INR"),
    )
    cols[3].metric(
        "Bank settled",
        _money(canonical.get("bank_amount"), canonical.get("currency") or "INR"),
    )
    st.markdown(
        " ".join(
            _pill(_label(reason), "warning") for reason in exception.exception_reasons
        ),
        unsafe_allow_html=True,
    )
    rows = []
    for pair in exception.pairwise:
        rows.append(
            {
                "comparison": _label(pair.pair),
                "status": pair.status,
                "difference": (
                    _money(pair.difference, canonical.get("currency") or "INR")
                    if pair.difference is not None
                    else "—"
                ),
                "matched by": ", ".join(_label(item) for item in pair.matched_by)
                or "—",
                "warnings": ", ".join(_label(item) for item in pair.warnings) or "—",
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")


def _render_validation(values: dict[str, Any]) -> None:
    validations = values.get("validations", [])
    if not validations:
        st.info("Validation has not run yet.")
        return
    for position, item in enumerate(validations):
        confidence = _confidence(item.get("confidence"))
        conclusion = item.get("conclusion", "INSUFFICIENT_EVIDENCE")
        with st.expander(
            f"{position + 1}. {_label(item.get('hypothesis'))} · {conclusion} · {confidence:.0%}",
            expanded=position == 0,
        ):
            st.progress(confidence, text=f"Confidence {confidence:.0%}")
            if item.get("calculations"):
                st.markdown("**Deterministic calculations**")
                for calculation in item["calculations"]:
                    st.code(str(calculation), language=None)
            if item.get("reasoning"):
                st.markdown("**Process summary**")
                for reason in item["reasoning"]:
                    st.write(f"• {reason}")
            cols = st.columns(3)
            cols[0].metric("Supports", len(item.get("evidence_for", [])))
            cols[1].metric("Opposes", len(item.get("evidence_against", [])))
            cols[2].metric("Missing", len(item.get("missing_evidence", [])))
            missing = item.get("missing_evidence", [])
            if missing:
                st.warning("\n\n".join(str(value) for value in missing))


def _render_challenge(values: dict[str, Any]) -> None:
    challenge = values.get("challenge", {})
    if not challenge:
        st.info("The independent challenger has not run yet.")
        return
    confidence = _confidence(challenge.get("adjusted_confidence"))
    cols = st.columns(3)
    cols[0].metric("Challenged cause", _label(challenge.get("hypothesis")))
    cols[1].metric("Adjusted confidence", f"{confidence:.0%}")
    cols[2].metric(
        "Human review",
        "Required" if challenge.get("force_human_review") else "Not required",
    )
    if challenge.get("final_explanation"):
        st.info(challenge["final_explanation"])
    checks = challenge.get("checks_performed", [])
    if checks:
        st.dataframe(
            [
                {
                    "result": "PASS" if check.get("passed") else "FAIL",
                    "counter-check": _label(check.get("name")),
                    "detail": check.get("detail"),
                }
                for check in checks
            ],
            hide_index=True,
            width="stretch",
        )
    contradictions = challenge.get("contradictory_evidence", [])
    unresolved = challenge.get("unresolved_questions", [])
    if contradictions:
        st.error(
            "Contradictions\n\n" + "\n\n".join(f"• {item}" for item in contradictions)
        )
    if unresolved:
        st.warning(
            "Unresolved questions\n\n" + "\n\n".join(f"• {item}" for item in unresolved)
        )


def _render_decision(values: dict[str, Any]) -> None:
    recommendation = values.get("recommendation", {})
    if not recommendation:
        st.info("The decision gate has not run yet.")
        return
    action = recommendation.get("action", "HUMAN_REVIEW")
    reason = recommendation.get("reason", "No reason was supplied.")
    if action == "AUTO_RESOLVE":
        st.success(f"AUTO-RESOLVE · {reason}")
    else:
        st.warning(f"{_label(action).upper()} · {reason}")
    cols = st.columns(3)
    cols[0].metric(
        "Root cause",
        _label(values.get("root_cause") or recommendation.get("resolution")),
    )
    cols[1].metric("Confidence", f"{_confidence(values.get('confidence')):.0%}")
    cols[2].metric("Control action", _label(action))
    missing_item = recommendation.get("missing_item")
    if missing_item:
        st.warning(f"Missing evidence: {missing_item}")
    if values.get("review"):
        decision = values["review"]
        st.info(f"Human decision recorded: {_label(decision.get('decision'))}")


def _render_review_checkpoint(invoice: str, recommendation: dict[str, Any]) -> None:
    action = recommendation.get("action", "HUMAN_REVIEW")
    if action == "REQUEST_MISSING_EVIDENCE":
        explanation = (
            "The agent abstained from assigning a cause. Acknowledge the evidence request "
            "or escalate the unresolved case; no automatic financial action will run."
        )
        approve_label = "Request missing evidence"
        reject_label = "Escalate unresolved case"
    elif action == "WAIT_FOR_SETTLEMENT":
        explanation = "The settlement is still pending. Confirm the hold or escalate for immediate review."
        approve_label = "Confirm settlement hold"
        reject_label = "Escalate now"
    else:
        explanation = (
            f"Proposed resolution: **{_label(recommendation.get('resolution'))}**. "
            "Approval applies it and records the reviewer override; rejection closes the case without applying it."
        )
        approve_label = "Approve recommendation"
        reject_label = "Reject and close"
    st.markdown("#### Review checkpoint")
    st.write(explanation)
    approve, reject = st.columns(2)
    with approve:
        if st.button(approve_label, type="primary", width="stretch"):
            _resume(invoice, "approve")
    with reject:
        if st.button(reject_label, width="stretch"):
            _resume(invoice, "reject")


def _headline(values: dict[str, Any]) -> str:
    try:
        from agents.narrative import compose_investigation_narrative

        narrative = compose_investigation_narrative(values)
        if narrative:
            return narrative
    except Exception:
        pass
    recommendation = values.get("recommendation", {})
    return str(recommendation.get("reason") or values.get("case_summary") or "")


def _render_final_reasoning(values: dict[str, Any]) -> None:
    try:
        text = _headline(values)
    except Exception:
        text = str(
            (values.get("recommendation") or {}).get("reason")
            or values.get("case_summary")
            or ""
        )
    if not text:
        return
    paragraphs = [html.escape(part.strip()) for part in text.split("\n\n") if part.strip()]
    if not paragraphs:
        return
    st.markdown("#### Final reasoning")
    st.markdown(
        '<div class="final-reasoning">' + "".join(f"<p>{part}</p>" for part in paragraphs) + "</div>",
        unsafe_allow_html=True,
    )


def _render_audit(values: dict[str, Any]) -> None:
    audit = values.get("audit")
    if audit:
        st.success(f"Resolution written · {_label(values.get('final_status'))}")
        st.dataframe([_plain(audit)], hide_index=True, width="stretch")
    elif values.get("recommendation"):
        st.info("The audit write is waiting for the decision gate to complete.")
    else:
        st.info("No audit event has been created yet.")
    if values.get("completed_at"):
        st.caption(f"Case memory stored at {values['completed_at']}")
    with st.expander("Developer trace · raw checkpoint state", expanded=False):
        st.caption(
            "Diagnostic view only. The investigation narrative above is the operator view."
        )
        st.json(_plain(values))


def _llm_status_caption() -> str:
    try:
        from config import LLM_ENABLED, LLM_MODEL, LLM_PROVIDER
    except Exception:
        return "Structured LLM interpretation is using the process default."
    if LLM_ENABLED:
        return (
            f"Structured LLM is on · {LLM_PROVIDER} · {LLM_MODEL}. "
            "Each model step shows parsed JSON only."
        )
    return "Structured LLM is off. Agents are using deterministic fallbacks; enable RECONCILIATION_LLM_ENABLED to watch live model JSON."


def _stream_graph(invoice: str, pending: dict[str, Any]) -> None:
    from langgraph.types import Command

    st.title(f"Invoice {invoice}")
    st.caption(
        "Live graph · each agent, tool, and structured LLM JSON object appears as it finishes."
    )
    st.caption(_llm_status_caption())
    status_box = st.status("Running the investigation graph…", expanded=True)
    now = st.empty()
    live = st.empty()
    events: list[dict[str, Any]] = []

    def render() -> None:
        latest = events[-1] if events else None
        with now.container():
            if latest:
                kind = str(latest.get("kind") or "step").upper()
                name = str(latest.get("name") or "")
                status = str(latest.get("status") or "")
                summary = str(latest.get("summary") or "")
                st.markdown(
                    '<div class="live-now">'
                    f'<div class="live-now-label">{html.escape(kind)} · {html.escape(status)}</div>'
                    f'<div class="live-now-text">{html.escape(name)} — {html.escape(summary)}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown("Starting the checkpointed LangGraph run…")
        with live.container():
            _render_stream_log(events)

    def paint() -> None:
        try:
            render()
        except Exception as exc:
            if "same key" not in str(exc).lower():
                raise

    if pending.get("mode") == "resume":
        payload: Any = Command(resume={"decision": pending.get("decision")})
    else:
        _reset_graph_thread(invoice)
        payload = {"invoice_id": invoice}
    paint()
    try:
        for mode, chunk in _graph_app().stream(
            payload,
            config={"configurable": {"thread_id": invoice}},
            stream_mode=["custom", "updates"],
        ):
            if mode == "custom" and isinstance(chunk, dict) and chunk.get("event_id"):
                events = _merge_events(events, [chunk])
                with status_box:
                    st.write(
                        str(chunk.get("summary") or chunk.get("name") or "Graph event")
                    )
                paint()
                pause = (
                    LLM_EVENT_PAUSE_SECONDS
                    if chunk.get("kind") == "llm"
                    else LIVE_EVENT_PAUSE_SECONDS
                )
                time.sleep(pause)
            elif mode == "updates" and isinstance(chunk, dict):
                for update in chunk.values():
                    if isinstance(update, dict) and update.get("agent_trace"):
                        events = _merge_events(events, list(update["agent_trace"]))
                paint()
    except Exception as exc:
        if "same key" in str(exc).lower():
            paint()
        else:
            status_box.update(label="Investigation failed", state="error")
            st.error(f"The investigation graph stopped: {exc}")
            return
    st.session_state.live_trace = events
    snapshot = _graph_app().get_state({"configurable": {"thread_id": invoice}})
    values = snapshot.values or {}
    paused = bool(snapshot.next)
    _render_live_activity(events, invoice)
    if paused:
        status_box.update(label="Paused for human review", state="complete")
        st.warning(
            "Decision checkpoint reached · agent execution is paused for controlled human review."
        )
        _render_review_checkpoint(invoice, values.get("recommendation") or {})
    else:
        status_box.update(label="Investigation complete", state="complete")
        st.success(
            "Graph stream finished. Structured LLM JSON and tool results are in the live log above."
        )


def investigation(case: dict[str, Any]) -> None:
    invoice = case["invoice_id"]
    if st.button("← Back to exception queue"):
        st.session_state.selected_invoice = None
        st.session_state.pending_graph_run = None
        st.rerun()
    pending = st.session_state.get("pending_graph_run")
    if pending:
        _stream_graph(invoice, pending)
        st.session_state.pending_graph_run = None
        st.rerun()
    try:
        snapshot = _graph_app().get_state({"configurable": {"thread_id": invoice}})
        values = snapshot.values or {}
        paused = bool(snapshot.next)
    except Exception as exc:
        st.error(f"Unable to load this graph checkpoint: {exc}")
        return
    if not values:
        st.info(
            "This invoice has no investigation checkpoint yet. Start it from the exception queue."
        )
        return
    recommendation = values.get("recommendation", {})
    root_cause = values.get("root_cause") or recommendation.get("resolution")
    status = _status(invoice)
    st.title(f"Invoice {invoice}")
    st.caption(
        f"Case {values.get('case_id', 'pending')} · checkpointed graph thread · {status}"
    )
    if st.button(
        "Watch graph live",
        help="Clears this thread and streams each agent, tool, and LLM JSON step.",
    ):
        _start(invoice)
        return
    try:
        _render_final_reasoning(values)
    except Exception:
        pass
    cols = st.columns(4)
    cols[0].metric(
        "Difference",
        (
            "Pending settlement"
            if case.get("bank_missing")
            else _money(case["difference"])
        ),
    )
    cols[1].metric("Leading cause", _label(root_cause))
    cols[2].metric("Confidence", f"{_confidence(values.get('confidence')):.0%}")
    cols[3].metric("Next action", _label(recommendation.get("action") or status))
    if paused:
        st.warning(
            "Decision checkpoint reached · agent execution is paused for controlled human review."
        )
        _render_review_checkpoint(invoice, recommendation)
    elif values.get("completed_at"):
        st.success(
            f"Investigation complete · {_label(values.get('final_status') or status)}"
        )
    activity_tab, evidence_tab, decision_tab, audit_tab = st.tabs(
        ["Agent activity", "Evidence", "Decision", "Audit"]
    )
    with activity_tab:
        st.subheader("Agent activity")
        st.caption(
            "The investigation path is horizontal. Open a stage to inspect its results, tool calls, and structured JSON. Internal hidden reasoning is never displayed."
        )
        st.caption(_llm_status_caption())
        if not any(
            event.get("kind") == "llm" for event in values.get("agent_trace") or []
        ):
            st.info(
                "No structured LLM JSON is in this trace. "
                "Set RECONCILIATION_LLM_ENABLED=true and a provider API key, then watch the graph live."
            )
        _render_timeline(values, paused, invoice=invoice)
    with evidence_tab:
        st.subheader("Source reconciliation")
        _render_exception(values)
        st.markdown("#### Evidence retrieval")
        _render_tool_calls(values, invoice)
    with decision_tab:
        try:
            _render_final_reasoning(values)
        except Exception:
            pass
        st.subheader("Hypothesis validation")
        _render_validation(values)
        st.markdown("#### Independent challenge")
        _render_challenge(values)
        st.markdown("#### Control decision")
        _render_decision(values)
    with audit_tab:
        st.subheader("Audit and memory")
        _render_audit(values)


def _queue(cases: list[dict[str, Any]], statuses: dict[str, str]) -> None:
    st.subheader("Exception queue")
    st.caption(
        "Select an invoice to watch the investigation graph, evidence calls, challenge, and control decision."
    )
    demo_order = {
        "INV-DEMO-FEE-0001": 0,
        "INV-DEMO-REFUND-0001": 1,
        "INV-DEMO-AMBIG-0001": 2,
        "INV-DEMO-UNKNOWN-0001": 3,
    }
    ordered = sorted(
        cases,
        key=lambda item: (demo_order.get(item["invoice_id"], 99), item["invoice_id"]),
    )
    for case in ordered[:30]:
        invoice = case["invoice_id"]
        scenario = {
            "INV-DEMO-FEE-0001": "Easy win",
            "INV-DEMO-REFUND-0001": "Human review",
            "INV-DEMO-AMBIG-0001": "Ambiguous",
            "INV-DEMO-UNKNOWN-0001": "Unknown cause",
        }.get(invoice, "Exception")
        with st.container(border=True):
            difference = (
                "Pending settlement"
                if case.get("bank_missing")
                else _money(case["difference"])
            )
            st.markdown(
                '<div class="queue-grid">'
                f'<div><div class="queue-label">Invoice</div><div class="queue-value">{html.escape(invoice)}</div></div>'
                f'<div><div class="queue-label">Difference</div><div class="queue-value">{html.escape(difference)}</div></div>'
                f'<div><div class="queue-label">Status</div><div class="queue-value">{html.escape(_label(statuses.get(invoice, "OPEN")))}</div></div>'
                f'<div><div class="queue-label">Scenario</div><div class="queue-value">{html.escape(scenario)}</div></div>'
                "</div>",
                unsafe_allow_html=True,
            )
            if st.button("Investigate", key=f"investigate-{invoice}", width="stretch"):
                _start(invoice)


def main() -> None:
    st.set_page_config(
        page_title="Reconciliation Investigator", page_icon="◎", layout="wide"
    )
    _page_styles()
    st.session_state.setdefault("selected_invoice", None)
    st.session_state.setdefault("pending_graph_run", None)
    st.session_state.setdefault("live_trace", [])
    cases = _cases()
    if st.session_state.selected_invoice:
        case = next(
            item
            for item in cases
            if item["invoice_id"] == st.session_state.selected_invoice
        )
        investigation(case)
        return
    st.title("Reconciliation Investigator")
    st.caption(
        "Explainable, checkpointed investigations across ERP, gateway, and bank data."
    )
    _queue(cases, _statuses())


if __name__ == "__main__":
    main()
