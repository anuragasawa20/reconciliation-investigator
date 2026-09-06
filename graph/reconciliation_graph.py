"""End-to-end reconciliation graph and its small, deterministic adapters."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.challenger import challenge_hypotheses
from agents.evidence_collector import collect_evidence
from agents.planner import plan_investigation
from agents.recommendation import EvidenceFact, HypothesisResult, recommend_action
from agents.validator import validate_hypotheses
from core.loader import BANK_REQUIRED, ERP_REQUIRED, GATEWAY_REQUIRED, LoadedDatasets, load_csv
from core.matcher import ReconciliationRecord, reconcile
from core.normalizer import normalize_datasets
from tools.memory_tools import apply_resolution, store_case_memory
from tools.lookups import search_historical_cases


CHALLENGE_RETRY_CAP = 2
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
STATE_DB_PATH = Path(__file__).resolve().parents[1] / "state.db"


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
    resolution: str
    final_status: str
    audit: dict[str, Any]


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
    return {"exception": _load_exception(state["invoice_id"]), "case_id": f"CASE-{state['invoice_id']}"}


def planner_node(state: ReconciliationState) -> dict[str, Any]:
    plan = plan_investigation(state["exception"])
    history = search_historical_cases({"invoice_id": state["invoice_id"]})
    return {"live_hypotheses": list(plan.live_hypotheses), "historical_cases": history["records"]}


def _facts_for_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert lookup records into validator facts with explicit source lineage."""
    facts: list[dict[str, Any]] = []
    for tool_name, result in bundle.items():
        records = result.get("records", [])
        status = result.get("status", "NOT_FOUND")
        if not records:
            facts.append({"fact": tool_name, "status": status, "source": result.get("source", "unknown")})
        for record in records:
            if not isinstance(record, dict):
                continue
            source = result.get("source", "unknown").split("+")[0]
            record_id = record.get("transaction_id") or record.get("settlement_id")
            for key, value in record.items():
                if key in {"invoice_id", "transaction_id", "settlement_id", "currency"} or value in (None, ""):
                    continue
                facts.append({"fact": key, "value": value, "source": source, "source_record_id": record_id, "status": status})
            facts.append({"fact": tool_name, "value": record, "source": source, "source_record_id": record_id, "status": status})
    return facts


def evidence_node(state: ReconciliationState) -> dict[str, Any]:
    raw = collect_evidence(state["live_hypotheses"], state["invoice_id"])
    return {"evidence_bundles": raw, "evidence": {key: _facts_for_bundle(value) for key, value in raw.items()}}


def validation_node(state: ReconciliationState) -> dict[str, Any]:
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
    return {"validations": payload}


def challenge_node(state: ReconciliationState) -> dict[str, Any]:
    try:
        result = challenge_hypotheses(
            state["exception"], state["validations"], state.get("historical_cases", [])
        )
        return {"challenge": {"hypothesis": result.hypothesis, "adjusted_confidence": str(result.adjusted_confidence),
                              "force_human_review": result.force_human_review, "final_explanation": result.final_explanation,
                              "contradictory_evidence": result.contradictory_evidence, "unresolved_questions": result.unresolved_questions,
                              "checks_performed": [check.__dict__ for check in result.checks_performed]},
                "challenge_error": ""}
    except Exception as exc:  # bounded retry is deliberately part of graph state
        retries = state.get("challenge_retry_count", 0) + 1
        return {"challenge_retry_count": retries, "challenge_error": f"{type(exc).__name__}: {exc}"}


def root_cause_node(state: ReconciliationState) -> dict[str, Any]:
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
    return {"recommendation": recommendation.as_dict(), "resolution": recommendation.resolution,
            "root_cause": hypothesis, "confidence": str(confidence), "case_summary": challenge.get("final_explanation") or recommendation.reason}


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
    return "apply_resolution" if state.get("recommendation", {}).get("action") == "AUTO_RESOLVE" else "review"


def review_node(state: ReconciliationState) -> dict[str, Any]:
    answer = interrupt({"type": "human_review", "invoice_id": state["invoice_id"],
                        "recommendation": state.get("recommendation", {}),
                        "reason": state.get("challenge_error") or state.get("recommendation", {}).get("reason")})
    decision = answer.get("decision") if isinstance(answer, dict) else answer
    approved = str(decision or "").strip().lower() in {"approve", "approved", "resolve", "yes"}
    return {"review": {"decision": decision, "approved": approved}, "resolution": state.get("resolution", "unknown"),
            "final_status": "HUMAN_APPROVED" if approved else "HUMAN_REJECTED"}


def apply_resolution_node(state: ReconciliationState) -> dict[str, Any]:
    review = state.get("review", {})
    approved = review.get("approved", True)
    resolution = state.get("resolution", "unknown") if approved else "REJECTED"
    status = state.get("final_status") or ("AUTO_RESOLVED" if not review else "HUMAN_APPROVED")
    audit = apply_resolution(invoice_id=state["invoice_id"], case_id=state.get("case_id"), resolution=resolution,
                             status=status, proposed_resolution=state.get("recommendation", {}).get("resolution"),
                             human_override=None if not review else str(review.get("decision")),
                             reason=state.get("recommendation", {}).get("reason", ""))
    return {"resolution": resolution, "final_status": status, "audit": audit}


def store_memory_node(state: ReconciliationState) -> dict[str, Any]:
    record = state["exception"]
    store_case_memory(invoice_id=state["invoice_id"], case_id=state.get("case_id"), pattern=state.get("root_cause", "unknown_other"),
                      resolution=state.get("resolution", "unknown"), confidence=float(state.get("confidence", "0")),
                      difference_minor=int(_difference(record) * 100), currency=record.canonical_transaction.get("currency") or "INR",
                      case_summary=state.get("case_summary", ""), human_override=state.get("review", {}).get("decision"))
    return {"completed_at": datetime.now(timezone.utc).isoformat()}


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
    connection = sqlite3.connect(str(state_db), check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    return build_graph(saver)


app = build_app()
