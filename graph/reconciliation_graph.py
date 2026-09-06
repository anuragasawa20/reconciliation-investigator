"""End-to-end reconciliation graph and its small, deterministic adapters."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.challenger import challenge_hypotheses
from agents.evidence_collector import collect_evidence
from agents.llm import jsonable
from agents.planner import plan_investigation
from agents.recommendation import EvidenceFact, HypothesisResult, recommend_action
from agents.trace import emit_live_trace, make_trace_event, trace_session
from agents.validator import validate_hypotheses
from core.loader import BANK_REQUIRED, ERP_REQUIRED, GATEWAY_REQUIRED, LoadedDatasets, load_csv
from core.matcher import ReconciliationRecord, reconcile
from core.normalizer import normalize_datasets
from tools.memory_tools import apply_resolution, store_case_memory
from tools.lookups import search_historical_cases


CHALLENGE_RETRY_CAP = 2
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
STATE_DB_PATH = Path(__file__).resolve().parents[1] / "state.db"
RUNTIME_DB_PATH = STATE_DB_PATH
app = None


def _merge_agent_trace(
    current: list[dict[str, Any]] | None,
    updates: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge trace updates in order and replace duplicate stable event IDs."""
    merged = [dict(event) for event in current or []]
    positions = {
        event.get("event_id"): index
        for index, event in enumerate(merged)
        if event.get("event_id")
    }
    for update in updates or []:
        event = dict(update)
        event_id = event.get("event_id")
        if not event_id:
            raise ValueError("Agent trace events require a stable event_id")
        if event_id in positions:
            merged[positions[event_id]] = event
        else:
            positions[event_id] = len(merged)
            merged.append(event)
    return merged


def _trace_event(
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
    """Build the compact, user-safe event contract consumed by the UI."""
    return make_trace_event(
        event_id=event_id,
        node=node,
        kind=kind,
        name=name,
        status=status,
        summary=summary,
        inputs=inputs,
        outputs=outputs,
        evidence_count=evidence_count,
    )


def _publish(**kwargs: Any) -> dict[str, Any]:
    event = _trace_event(**kwargs)
    emit_live_trace(event)
    return event


def _result_count(result: dict[str, Any]) -> int:
    count = result.get("count")
    if isinstance(count, int):
        return count
    records = result.get("records", [])
    return len(records) if isinstance(records, list) else 0


def _tool_trace_event(
    *,
    node: str,
    event_id: str,
    tool_name: str,
    result: dict[str, Any],
    fallback_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    count = _result_count(result)
    status = str(result.get("status", "UNKNOWN")).upper()
    source = str(result.get("source", "unknown"))
    query = result.get("query")
    inputs = (
        {key: value for key, value in query.items() if value is not None}
        if isinstance(query, dict)
        else (fallback_inputs or {})
    )
    record_label = "record" if count == 1 else "records"
    records = result.get("records", [])
    return _trace_event(
        event_id=event_id,
        node=node,
        kind="tool",
        name=tool_name,
        status=status,
        summary=f"{tool_name} returned {count} {record_label} from {source}.",
        inputs=inputs,
        outputs={
            "source": source,
            "retrieval_status": status,
            "record_count": count,
            "records": jsonable(records) if isinstance(records, list) else [],
        },
        evidence_count=count,
    )


class ReconciliationState(TypedDict, total=False):
    invoice_id: str
    case_id: str
    exception: ReconciliationRecord
    live_hypotheses: list[str]
    evidence_bundles: dict[str, dict[str, Any]]
    validations: list[dict[str, Any]]
    historical_cases: list[dict[str, Any]]
    challenge: dict[str, Any]
    challenge_retry_count: int
    challenge_error: str
    recommendation: dict[str, Any]
    review: dict[str, Any]
    root_cause: str
    confidence: str
    case_summary: str
    resolution: str
    final_status: str
    audit: dict[str, Any]
    completed_at: str
    agent_trace: Annotated[list[dict[str, Any]], _merge_agent_trace]


def _load_exception(invoice_id: str) -> ReconciliationRecord:
    loaded = LoadedDatasets(
        erp=load_csv(DATA_DIR / "erp.csv", "erp", ERP_REQUIRED),
        gateway=load_csv(DATA_DIR / "gateway.csv", "gateway", GATEWAY_REQUIRED),
        bank=load_csv(DATA_DIR / "bank.csv", "bank", BANK_REQUIRED),
    )
    record = next((item for item in reconcile(normalize_datasets(loaded)).records if item.invoice_id == invoice_id), None)
    if record is None:
        raise ValueError(f"Invoice {invoice_id!r} was not found in the reconciliation data")
    if not record.is_exception:
        raise ValueError(f"Invoice {invoice_id!r} is matched and does not need investigation")
    return record


def load_exception_node(state: ReconciliationState) -> dict[str, Any]:
    invoice_id = state["invoice_id"]
    with trace_session("load_exception") as events:
        _publish(
            event_id="node:load_exception",
            node="load_exception",
            kind="node",
            name="Load exception",
            status="RUNNING",
            summary=f"Loading ERP, gateway, and bank records for {invoice_id}.",
            inputs={"invoice_id": invoice_id},
        )
        record = _load_exception(invoice_id)
        case_id = f"CASE-{invoice_id}"
        _publish(
            event_id="node:load_exception",
            node="load_exception",
            kind="node",
            name="Load exception",
            status="COMPLETED",
            summary=f"Isolated {invoice_id} as a reconciliation exception.",
            inputs={"invoice_id": invoice_id},
            outputs={
                "case_id": case_id,
                "match_status": record.match_status,
                "exception_reasons": list(record.exception_reasons),
                "discrepancy": str(_difference(record)),
                "source_record_counts": {
                    "erp": len(record.erp),
                    "gateway": len(record.gateway),
                    "bank": len(record.bank),
                },
            },
            evidence_count=len(record.erp) + len(record.gateway) + len(record.bank),
        )
        return {
            "exception": record,
            "case_id": case_id,
            "agent_trace": list(events),
        }


def _historical_patterns(exception: ReconciliationRecord) -> list[str]:
    gateway = exception.gateway[0] if len(exception.gateway) == 1 else None
    patterns: list[str] = []
    if gateway and gateway.fee and gateway.fee > 0:
        patterns.append("gateway_fee")
    if gateway and gateway.refund_amount and gateway.refund_amount > 0:
        patterns.append("refund")
    if not exception.bank or "settlement_outside_window" in exception.exception_reasons:
        patterns.append("timing_difference")
    if not patterns:
        patterns.append("unknown_other")
    return patterns


def _similar_historical_cases(
    exception: ReconciliationRecord, limit: int = 5
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    currency = exception.canonical_transaction.get("currency") or "INR"
    lookups: list[tuple[str, dict[str, Any]]] = []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern in _historical_patterns(exception):
        event_id = f"tool:plan:search_historical_cases:{pattern}"
        _publish(
            event_id=event_id,
            node="plan",
            kind="tool",
            name="search_historical_cases",
            status="RUNNING",
            summary=f"Searching historical cases for pattern {pattern}.",
            inputs={"currency": currency, "pattern": pattern, "exclude_invoice_id": exception.invoice_id},
        )
        result = search_historical_cases(
            {"currency": currency, "pattern": pattern}, limit=limit + 1
        )
        filtered = [
            row for row in result.get("records", [])
            if row.get("invoice_id") != exception.invoice_id
        ]
        visible_result = {
            **result,
            "records": filtered,
            "count": len(filtered),
            "status": "FOUND" if filtered else "NOT_FOUND",
            "query": {
                "currency": currency,
                "pattern": pattern,
                "exclude_invoice_id": exception.invoice_id,
            },
        }
        lookups.append((pattern, visible_result))
        emit_live_trace(
            _tool_trace_event(
                node="plan",
                event_id=f"tool:plan:search_historical_cases:{pattern}",
                tool_name="search_historical_cases",
                result=visible_result,
            )
        )
        for row in filtered:
            identity = str(row.get("case_id") or row.get("invoice_id"))
            if identity not in seen and len(records) < limit:
                seen.add(identity)
                records.append(row)
    return records, lookups


def planner_node(state: ReconciliationState) -> dict[str, Any]:
    with trace_session("plan") as events:
        _publish(
            event_id="node:plan",
            node="plan",
            kind="node",
            name="Plan investigation",
            status="RUNNING",
            summary="Selecting bounded hypotheses for this exception.",
            inputs={
                "invoice_id": state["invoice_id"],
                "exception_reasons": list(state["exception"].exception_reasons),
            },
        )
        historical_cases, _historical_lookups = _similar_historical_cases(
            state["exception"]
        )
        plan = plan_investigation(state["exception"], historical_cases)
        hypotheses = list(plan.live_hypotheses)
        history_count = len(historical_cases)
        _publish(
            event_id="node:plan",
            node="plan",
            kind="node",
            name="Plan investigation",
            status="COMPLETED",
            summary=f"Selected {len(hypotheses)} bounded hypotheses for investigation.",
            inputs={
                "invoice_id": state["invoice_id"],
                "exception_reasons": list(state["exception"].exception_reasons),
            },
            outputs={"ranked_hypotheses": hypotheses, "historical_case_count": history_count},
            evidence_count=history_count,
        )
        return {
            "live_hypotheses": hypotheses,
            "historical_cases": historical_cases,
            "agent_trace": list(events),
        }


def _facts_for_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert lookup records into validator facts with explicit source lineage."""
    facts: list[dict[str, Any]] = []
    for tool_name, result in bundle.items():
        fact_name = tool_name.removeprefix("get_")
        records = result.get("records", [])
        status = result.get("status", "NOT_FOUND")
        if not records:
            facts.append({"fact": fact_name, "status": status, "source": result.get("source", "unknown")})
        for record in records:
            if not isinstance(record, dict):
                continue
            source = result.get("source", "unknown").split("+")[0]
            record_id = record.get("transaction_id") or record.get("settlement_id")
            for key, value in record.items():
                if key in {"invoice_id", "transaction_id", "settlement_id", "currency"} or value in (None, ""):
                    continue
                facts.append({"fact": key, "value": value, "source": source, "source_record_id": record_id, "status": status})
            facts.append({"fact": fact_name, "value": record, "source": source, "source_record_id": record_id, "status": status})
    return facts


def evidence_node(state: ReconciliationState) -> dict[str, Any]:
    with trace_session("collect_evidence") as events:
        hypotheses = list(state["live_hypotheses"])
        _publish(
            event_id="node:collect_evidence",
            node="collect_evidence",
            kind="node",
            name="Collect evidence",
            status="RUNNING",
            summary="Calling deterministic source tools for the live hypotheses.",
            inputs={"invoice_id": state["invoice_id"], "hypotheses": hypotheses},
        )

        def on_result(phase: str, hypothesis: str, tool_name: str, result: dict[str, Any] | None) -> None:
            event_id = f"tool:collect_evidence:{hypothesis}:{tool_name}"
            if phase == "start":
                _publish(
                    event_id=event_id,
                    node="collect_evidence",
                    kind="tool",
                    name=tool_name,
                    status="RUNNING",
                    summary=f"Calling {tool_name} for {_label_hypothesis(hypothesis)}.",
                    inputs={"invoice_id": state["invoice_id"], "hypothesis": hypothesis},
                )
                return
            emit_live_trace(
                _tool_trace_event(
                    node="collect_evidence",
                    event_id=event_id,
                    tool_name=tool_name,
                    result=result or {},
                    fallback_inputs={"invoice_id": state["invoice_id"], "hypothesis": hypothesis},
                )
            )

        raw = collect_evidence(
            hypotheses, state["invoice_id"], on_result=on_result
        )
        tool_events = [event for event in events if event.get("kind") == "tool"]
        evidence_count = sum(int(event.get("evidence_count") or 0) for event in tool_events)
        _publish(
            event_id="node:collect_evidence",
            node="collect_evidence",
            kind="node",
            name="Collect evidence",
            status="COMPLETED",
            summary=f"Ran {len(tool_events)} deterministic evidence lookups across the live hypotheses.",
            inputs={"invoice_id": state["invoice_id"], "hypotheses": hypotheses},
            outputs={"tool_call_count": len(tool_events), "records_returned": evidence_count},
            evidence_count=evidence_count,
        )
        return {
            "evidence_bundles": raw,
            "evidence": {key: _facts_for_bundle(value) for key, value in raw.items()},
            "agent_trace": list(events),
        }


def _label_hypothesis(hypothesis: str) -> str:
    return hypothesis.replace("_", " ")


def validation_node(state: ReconciliationState) -> dict[str, Any]:
    with trace_session("validate") as events:
        _publish(
            event_id="node:validate",
            node="validate",
            kind="node",
            name="Validate hypotheses",
            status="RUNNING",
            summary="Testing each hypothesis against source-backed facts.",
            inputs={"hypotheses": list(state["live_hypotheses"])},
        )
        facts = {key: _facts_for_bundle(value) for key, value in state["evidence_bundles"].items() if key != "unknown_other"}
        requested = ["unknown" if item == "unknown_other" else item for item in state["live_hypotheses"]]
        results = validate_hypotheses(facts, dict.fromkeys(requested))
        payload = []
        for result in results:
            item = result.to_dict()
            if item["hypothesis"] == "unknown":
                item["hypothesis"] = "unknown_other"
            if item["conclusion"] == "REJECTED":
                item["conclusion"] = "NOT_SUPPORTED"
            payload.append(item)
        validation_summary = [
            {
                "hypothesis": item.get("hypothesis"),
                "conclusion": item.get("conclusion"),
                "confidence": str(item.get("confidence", "0")),
                "supporting_evidence_count": len(item.get("evidence_for", [])),
                "contradicting_evidence_count": len(item.get("evidence_against", [])),
                "missing_evidence_count": len(item.get("missing_evidence", [])),
            }
            for item in payload
        ]
        _publish(
            event_id="node:validate",
            node="validate",
            kind="node",
            name="Validate hypotheses",
            status="COMPLETED",
            summary=f"Compared source-backed evidence for {len(payload)} hypotheses.",
            inputs={
                "hypotheses": list(state["live_hypotheses"]),
                "evidence_record_count": sum(
                    _result_count(result)
                    for bundle in state["evidence_bundles"].values()
                    for result in bundle.values()
                ),
            },
            outputs={"results": validation_summary},
            evidence_count=sum(len(item.get("evidence_for", [])) for item in payload),
        )
        return {
            "validations": payload,
            "agent_trace": list(events),
        }


def challenge_node(state: ReconciliationState) -> dict[str, Any]:
    attempt = state.get("challenge_retry_count", 0) + 1
    with trace_session("challenge") as events:
        _publish(
            event_id=f"node:challenge:{attempt}",
            node="challenge",
            kind="node",
            name="Challenge leading hypothesis",
            status="RUNNING",
            summary="Attempting to falsify the leading hypothesis.",
            inputs={
                "attempt": attempt,
                "hypothesis_count": len(state.get("validations", [])),
            },
        )
        try:
            result = challenge_hypotheses(
                state["exception"], state["validations"], state.get("historical_cases", [])
            )
            challenge = {"hypothesis": result.hypothesis, "adjusted_confidence": str(result.adjusted_confidence),
                         "force_human_review": result.force_human_review, "final_explanation": result.final_explanation,
                         "contradictory_evidence": result.contradictory_evidence, "unresolved_questions": result.unresolved_questions,
                         "checks_performed": [check.__dict__ for check in result.checks_performed]}
            _publish(
                event_id=f"node:challenge:{attempt}",
                node="challenge",
                kind="node",
                name="Challenge leading hypothesis",
                status="COMPLETED",
                summary=f"Ran {len(result.checks_performed)} falsification checks on the leading hypothesis.",
                inputs={
                    "attempt": attempt,
                    "hypothesis_count": len(state["validations"]),
                    "historical_case_count": len(state.get("historical_cases", [])),
                },
                outputs={
                    "leading_hypothesis": result.hypothesis or "unknown_other",
                    "checks_performed": len(result.checks_performed),
                    "contradiction_count": len(result.contradictory_evidence),
                    "unresolved_question_count": len(result.unresolved_questions),
                    "adjusted_confidence": str(result.adjusted_confidence),
                    "force_human_review": result.force_human_review,
                },
                evidence_count=len(result.checks_performed),
            )
            return {
                "challenge": challenge,
                "challenge_error": "",
                "agent_trace": list(events),
            }
        except Exception as exc:  # bounded retry is deliberately part of graph state
            _publish(
                event_id=f"node:challenge:{attempt}",
                node="challenge",
                kind="node",
                name="Challenge leading hypothesis",
                status="FAILED",
                summary="The challenger could not complete; bounded retry policy was applied.",
                inputs={"attempt": attempt, "retry_cap": CHALLENGE_RETRY_CAP},
                outputs={
                    "error_type": type(exc).__name__,
                    "will_retry": attempt < CHALLENGE_RETRY_CAP,
                },
            )
            if attempt >= CHALLENGE_RETRY_CAP:
                _publish(
                    event_id="node:review",
                    node="review",
                    kind="node",
                    name="Human review",
                    status="WAITING",
                    summary="The challenger retry cap was reached; the graph is paused for a reviewer.",
                    inputs={"invoice_id": state["invoice_id"], "challenge_attempts": attempt},
                    outputs={},
                )
            return {
                "challenge_retry_count": attempt,
                "challenge_error": f"{type(exc).__name__}: {exc}",
                "agent_trace": list(events),
            }


def root_cause_node(state: ReconciliationState) -> dict[str, Any]:
    with trace_session("root_cause") as events:
        _publish(
            event_id="node:root_cause",
            node="root_cause",
            kind="node",
            name="Select root cause and action",
            status="RUNNING",
            summary="Applying the control policy to the challenged hypothesis.",
            inputs={"invoice_id": state["invoice_id"]},
        )
        challenge = state.get("challenge", {})
        hypothesis = challenge.get("hypothesis") or "unknown_other"
        validation = next((item for item in state["validations"] if item.get("hypothesis") == hypothesis), None)
        validation = validation or {"confidence": "0", "evidence_for": [], "evidence_against": [], "missing_evidence": []}
        confidence = Decimal(str(challenge.get("adjusted_confidence", validation.get("confidence", "0"))))
        facts = tuple(_current_facts(state["exception"], hypothesis))
        result = HypothesisResult(hypothesis=hypothesis, resolution_label=hypothesis,
                                  confidence=confidence, evidence_for=facts,
                                  materiality_amount=_difference(state["exception"]),
                                  evidence_against=tuple(EvidenceFact(fact="contradiction", source="current", source_record_id=None, value=str(item)) for item in challenge.get("contradictory_evidence", [])),
                                  missing_evidence=tuple(validation.get("missing_evidence", [])),
                                  force_human_review=bool(challenge.get("force_human_review")))
        recommendation = recommend_action(result)
        recommendation_payload = recommendation.as_dict()
        case_summary = str(challenge.get("final_explanation") or recommendation.reason)
        try:
            from agents.narrative import compose_investigation_narrative

            composed = compose_investigation_narrative(
                {
                    "invoice_id": state.get("invoice_id"),
                    "exception": state.get("exception"),
                    "validations": state.get("validations") or [],
                    "challenge": challenge,
                    "recommendation": recommendation_payload,
                    "root_cause": hypothesis,
                    "confidence": str(confidence),
                }
            )
            if composed:
                case_summary = composed
        except Exception:
            pass
        _publish(
            event_id="node:root_cause",
            node="root_cause",
            kind="node",
            name="Select root cause and action",
            status="COMPLETED",
            summary=f"Selected {hypothesis} and routed the case to {recommendation_payload['action']}.",
            inputs={
                "challenged_hypothesis": hypothesis,
                "adjusted_confidence": str(confidence),
                "contradiction_count": len(challenge.get("contradictory_evidence", [])),
            },
            outputs={
                "root_cause": hypothesis,
                "resolution": recommendation.resolution,
                "action": recommendation_payload["action"],
                "human_intervention_required": recommendation_payload["human_intervention_required"],
            },
            evidence_count=len(facts),
        )
        if recommendation_payload.get("human_intervention_required"):
            _publish(
                event_id="node:review",
                node="review",
                kind="node",
                name="Human review",
                status="WAITING",
                summary="The graph is paused until a reviewer approves or rejects the proposed resolution.",
                inputs={
                    "invoice_id": state["invoice_id"],
                    "proposed_resolution": recommendation.resolution,
                },
                outputs={},
            )
        return {
            "recommendation": recommendation_payload,
            "resolution": recommendation.resolution,
            "root_cause": hypothesis,
            "confidence": str(confidence),
            "case_summary": case_summary,
            "agent_trace": list(events),
        }


def _current_facts(exception: ReconciliationRecord, hypothesis: str) -> list[EvidenceFact]:
    facts: list[EvidenceFact] = []
    erp = exception.erp[0] if len(exception.erp) == 1 else None
    gateway = exception.gateway[0] if len(exception.gateway) == 1 else None
    bank = exception.bank[0] if len(exception.bank) == 1 else None
    if erp:
        facts.append(EvidenceFact("expected_amount", "erp", erp.transaction_id, value=str(erp.amount)))
    if gateway:
        facts.append(EvidenceFact("gateway_transaction", "gateway", gateway.transaction_id, value=str(gateway.amount)))
        if hypothesis == "gateway_fee" and gateway.fee is not None:
            facts.append(EvidenceFact("gateway_fee", "gateway", gateway.transaction_id, value=str(gateway.fee)))
        if hypothesis == "refund" and gateway.refund_amount is not None and gateway.refund_amount > 0:
            facts.append(EvidenceFact("refund_record", "gateway", gateway.transaction_id, value=str(gateway.refund_amount)))
    if bank:
        facts.append(EvidenceFact("bank_settlement", "bank", bank.transaction_id, value=str(bank.settled_amount or bank.amount)))
    return facts


def _difference(exception: ReconciliationRecord) -> Decimal:
    canonical = exception.canonical_transaction
    expected, settled = canonical.get("expected_amount"), canonical.get("bank_amount")
    return abs(Decimal(expected or "0") - Decimal(settled or "0"))


def route_after_challenge(state: ReconciliationState) -> str:
    if state.get("challenge_error"):
        return "review" if state.get("challenge_retry_count", 0) >= CHALLENGE_RETRY_CAP else "challenge"
    return "root_cause"


def route_after_recommendation(state: ReconciliationState) -> str:
    action = state.get("recommendation", {}).get("action")
    return "apply_resolution" if action in {"AUTO_RESOLVE", "WAIT_FOR_SETTLEMENT"} else "review"


def review_node(state: ReconciliationState) -> dict[str, Any]:
    with trace_session("review") as events:
        _publish(
            event_id="node:review",
            node="review",
            kind="node",
            name="Human review",
            status="WAITING",
            summary="Paused for a controlled reviewer decision.",
            inputs={"invoice_id": state["invoice_id"]},
        )
        answer = interrupt({"type": "human_review", "invoice_id": state["invoice_id"],
                            "recommendation": state.get("recommendation", {}),
                            "reason": state.get("challenge_error") or state.get("recommendation", {}).get("reason")})
        decision = answer.get("decision") if isinstance(answer, dict) else answer
        approved = str(decision or "").strip().lower() in {"approve", "approved", "resolve", "yes"}
        final_status = "HUMAN_APPROVED" if approved else "HUMAN_REJECTED"
        _publish(
            event_id="node:review",
            node="review",
            kind="node",
            name="Human review",
            status="COMPLETED",
            summary=f"Reviewer decision recorded as {final_status}.",
            inputs={"proposed_resolution": state.get("resolution", "unknown")},
            outputs={"decision": decision, "approved": approved, "final_status": final_status},
        )
        return {
            "review": {"decision": decision, "approved": approved},
            "resolution": state.get("resolution", "unknown"),
            "final_status": final_status,
            "agent_trace": list(events),
        }


def apply_resolution_node(state: ReconciliationState) -> dict[str, Any]:
    with trace_session("apply_resolution") as events:
        _publish(
            event_id="node:apply_resolution",
            node="apply_resolution",
            kind="node",
            name="Apply resolution",
            status="RUNNING",
            summary="Writing the controlled resolution to the audit store.",
            inputs={"invoice_id": state["invoice_id"]},
        )
        review = state.get("review", {})
        approved = review.get("approved", True)
        resolution = state.get("resolution", "unknown") if approved else "REJECTED"
        action = state.get("recommendation", {}).get("action")
        automatic_status = (
            "WAITING_FOR_SETTLEMENT" if action == "WAIT_FOR_SETTLEMENT"
            else "AUTO_RESOLVED"
        )
        status = state.get("final_status") or (automatic_status if not review else "HUMAN_APPROVED")
        _publish(
            event_id="tool:apply_resolution:apply_resolution",
            node="apply_resolution",
            kind="tool",
            name="apply_resolution",
            status="RUNNING",
            summary="Persisting the case-status audit row.",
            inputs={"case_id": state.get("case_id"), "invoice_id": state["invoice_id"]},
        )
        audit = apply_resolution(invoice_id=state["invoice_id"], case_id=state.get("case_id"), resolution=resolution,
                                 status=status, proposed_resolution=state.get("recommendation", {}).get("resolution"),
                                 human_override=None if not review else str(review.get("decision")),
                                 reason=state.get("recommendation", {}).get("reason", ""),
                                 db_path=RUNTIME_DB_PATH)
        _publish(
            event_id="tool:apply_resolution:apply_resolution",
            node="apply_resolution",
            kind="tool",
            name="apply_resolution",
            status="COMPLETED",
            summary="Persisted the final decision to the case-status audit store.",
            inputs={"case_id": state.get("case_id"), "invoice_id": state["invoice_id"]},
            outputs={"status": audit.get("status"), "case_id": audit.get("case_id")},
        )
        _publish(
            event_id="node:apply_resolution",
            node="apply_resolution",
            kind="node",
            name="Apply resolution",
            status="COMPLETED",
            summary=f"Applied {resolution} with status {status}.",
            inputs={
                "invoice_id": state["invoice_id"],
                "proposed_resolution": state.get("recommendation", {}).get("resolution"),
                "review_required": bool(review),
            },
            outputs={"resolution": resolution, "final_status": status},
        )
        return {
            "resolution": resolution,
            "final_status": status,
            "audit": audit,
            "agent_trace": list(events),
        }


def store_memory_node(state: ReconciliationState) -> dict[str, Any]:
    with trace_session("store_case_memory") as events:
        _publish(
            event_id="node:store_case_memory",
            node="store_case_memory",
            kind="node",
            name="Store case memory",
            status="RUNNING",
            summary="Writing the resolved case into historical memory.",
            inputs={"case_id": state.get("case_id")},
        )
        record = state["exception"]
        _publish(
            event_id="tool:store_case_memory:store_case_memory",
            node="store_case_memory",
            kind="tool",
            name="store_case_memory",
            status="RUNNING",
            summary="Persisting the resolved case to reconciliation memory.",
            inputs={"invoice_id": state["invoice_id"], "case_id": state.get("case_id")},
        )
        memory = store_case_memory(invoice_id=state["invoice_id"], case_id=state.get("case_id"), pattern=state.get("root_cause", "unknown_other"),
                                   resolution=state.get("resolution", "unknown"), confidence=float(state.get("confidence", "0")),
                                   difference_minor=int(_difference(record) * 100), currency=record.canonical_transaction.get("currency") or "INR",
                                   case_summary=state.get("case_summary", ""), human_override=state.get("review", {}).get("decision"),
                                   db_path=RUNTIME_DB_PATH)
        completed_at = datetime.now(timezone.utc).isoformat()
        _publish(
            event_id="tool:store_case_memory:store_case_memory",
            node="store_case_memory",
            kind="tool",
            name="store_case_memory",
            status="COMPLETED",
            summary="Persisted the resolved case to reconciliation memory.",
            inputs={"invoice_id": state["invoice_id"], "case_id": state.get("case_id")},
            outputs={"resolution": memory.get("resolution"), "case_id": memory.get("case_id")},
        )
        _publish(
            event_id="node:store_case_memory",
            node="store_case_memory",
            kind="node",
            name="Store case memory",
            status="COMPLETED",
            summary="Stored the finalized case for future historical retrieval.",
            inputs={
                "case_id": state.get("case_id"),
                "pattern": state.get("root_cause", "unknown_other"),
                "resolution": state.get("resolution", "unknown"),
            },
            outputs={"case_id": memory.get("case_id"), "completed": True},
        )
        return {
            "completed_at": completed_at,
            "agent_trace": list(events),
        }


def build_graph(checkpointer: Any = None):
    graph = StateGraph(ReconciliationState)
    graph.add_node("load_exception", load_exception_node)
    graph.add_node("plan", planner_node)
    graph.add_node("collect_evidence", evidence_node)
    graph.add_node("validate", validation_node)
    graph.add_node("challenge", challenge_node)
    graph.add_node("root_cause", root_cause_node)
    graph.add_node("review", review_node)
    graph.add_node("apply_resolution", apply_resolution_node)
    graph.add_node("store_case_memory", store_memory_node)
    graph.add_edge(START, "load_exception")
    graph.add_edge("load_exception", "plan")
    graph.add_edge("plan", "collect_evidence")
    graph.add_edge("collect_evidence", "validate")
    graph.add_edge("validate", "challenge")
    graph.add_conditional_edges("challenge", route_after_challenge, {"challenge": "challenge", "root_cause": "root_cause", "review": "review"})
    graph.add_conditional_edges("root_cause", route_after_recommendation, {"apply_resolution": "apply_resolution", "review": "review"})
    graph.add_edge("review", "apply_resolution")
    graph.add_edge("apply_resolution", "store_case_memory")
    graph.add_edge("store_case_memory", END)
    return graph.compile(checkpointer=checkpointer)


def build_app(state_db: str | Path = STATE_DB_PATH):
    global RUNTIME_DB_PATH
    RUNTIME_DB_PATH = Path(state_db)
    connection = sqlite3.connect(str(RUNTIME_DB_PATH), check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    return build_graph(saver)


def get_app(state_db: str | Path = STATE_DB_PATH):
    global app
    if app is None:
        app = build_app(state_db)
    return app


# Keep the module import lightweight; the app is initialized on first use.
