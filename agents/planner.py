from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from agents.llm import StructuredLLMClient, default_llm_client, jsonable
from core.matcher import ReconciliationRecord
from core.normalizer import NormalizedRecord
from schemas import HistoricalCase, Hypothesis, HYPOTHESIS_TAXONOMY


@dataclass(frozen=True)
class InvestigationPlan:
    invoice_id: str
    live_hypotheses: list[Hypothesis]


def plan_investigation(
    exception: ReconciliationRecord,
    historical_cases: Sequence[HistoricalCase | Mapping[str, object]] | None = None,
    *,
    llm_client: StructuredLLMClient | None = None,
    use_llm: bool | None = None,
) -> InvestigationPlan:
    """Return the bounded hypotheses still worth investigating for an exception."""

    active_client = llm_client if use_llm is not False else None
    if active_client is None and use_llm is not False:
        active_client = default_llm_client()
    if active_client is not None:
        return _plan_with_llm(exception, historical_cases or [], active_client)

    return _plan_deterministically(exception, historical_cases or [])


def _plan_deterministically(
    exception: ReconciliationRecord,
    historical_cases: Sequence[HistoricalCase | Mapping[str, object]],
) -> InvestigationPlan:
    """Deterministic planner used for tests, offline demos, and LLM fallback."""

    historical_hypotheses = _historical_hypotheses(historical_cases)
    live: list[Hypothesis] = []

    for hypothesis in HYPOTHESIS_TAXONOMY:
        if hypothesis == "unknown_other":
            continue
        if _is_live(hypothesis, exception, historical_hypotheses):
            live.append(hypothesis)

    live = _ordered_by_signal(live, exception, historical_hypotheses)
    live.append("unknown_other")
    return InvestigationPlan(invoice_id=exception.invoice_id, live_hypotheses=live)


def _plan_with_llm(
    exception: ReconciliationRecord,
    historical_cases: Sequence[HistoricalCase | Mapping[str, object]],
    llm_client: StructuredLLMClient,
) -> InvestigationPlan:
    deterministic = _plan_deterministically(exception, historical_cases)
    payload = {
        "exception": _exception_payload(exception),
        "historical_cases": jsonable(historical_cases),
        "allowed_hypotheses": list(HYPOTHESIS_TAXONOMY),
        "deterministic_candidate_plan": deterministic.live_hypotheses,
    }
    output = llm_client.complete_json(
        task="investigation_plan",
        system_prompt=(
            "You are the hypothesis planner for a financial reconciliation investigator. "
            "Select only hypotheses from the allowed taxonomy. Keep unknown_other as the "
            "last fallback. Do not invent transactions, amounts, dates, or causes."
        ),
        user_payload=payload,
        schema=_PLANNER_SCHEMA,
    )
    hypotheses = _coerce_hypotheses(output.get("live_hypotheses"))
    if "unknown_other" not in hypotheses:
        hypotheses.append("unknown_other")
    hypotheses = list(dict.fromkeys(hypotheses))
    return InvestigationPlan(invoice_id=exception.invoice_id, live_hypotheses=hypotheses)


_PLANNER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "live_hypotheses": {
            "type": "array",
            "items": {"type": "string", "enum": list(HYPOTHESIS_TAXONOMY)},
        }
    },
    "required": ["live_hypotheses"],
}


def _coerce_hypotheses(value: object) -> list[Hypothesis]:
    if not isinstance(value, list):
        raise ValueError("Planner LLM output must include live_hypotheses as a list.")
    hypotheses: list[Hypothesis] = []
    for item in value:
        if item not in HYPOTHESIS_TAXONOMY:
            raise ValueError(f"Planner returned unknown hypothesis: {item!r}")
        hypotheses.append(item)
    return hypotheses


def _exception_payload(exception: ReconciliationRecord) -> dict[str, object]:
    pairwise_differences = {
        match.pair: str(match.difference)
        for match in exception.pairwise
        if match.difference is not None
    }
    return {
        "invoice_id": exception.invoice_id,
        "match_status": exception.match_status,
        "exception_reasons": list(exception.exception_reasons),
        "matched_by": list(exception.matched_by),
        "warnings": list(exception.warnings),
        "canonical_transaction": exception.canonical_transaction,
        "gross_difference": pairwise_differences.get("erp_gateway"),
        "settlement_difference": pairwise_differences.get("gateway_bank")
        or pairwise_differences.get("erp_bank"),
        "erp_count": len(exception.erp),
        "gateway_count": len(exception.gateway),
        "bank_count": len(exception.bank),
        "erp": jsonable(exception.erp),
        "gateway": jsonable(exception.gateway),
        "bank": jsonable(exception.bank),
        "pairwise": jsonable(exception.pairwise),
    }


def _is_live(
    hypothesis: Hypothesis,
    exception: ReconciliationRecord,
    historical_hypotheses: set[Hypothesis],
) -> bool:
    if hypothesis == "gateway_fee":
        return _has_recorded_fee(exception) or (
            "gateway_fee" in historical_hypotheses and _has_bank_shortfall(exception)
        )
    if hypothesis == "refund":
        return _has_recorded_refund(exception) or (
            "refund" in historical_hypotheses and _has_bank_shortfall(exception) and not _fee_fully_explains_shortfall(exception)
        )
    if hypothesis == "timing_difference":
        return "settlement_outside_window" in exception.exception_reasons or _has_missing_bank_record(exception)
    if hypothesis == "manual_adjustment":
        return _has_unexplained_one_to_one_amount_mismatch(exception)
    if hypothesis == "duplicate_or_missing_transaction":
        return _has_duplicate_or_missing_source(exception)
    return True


def _ordered_by_signal(
    live: list[Hypothesis],
    exception: ReconciliationRecord,
    historical_hypotheses: set[Hypothesis],
) -> list[Hypothesis]:
    def rank(hypothesis: Hypothesis) -> tuple[int, int]:
        exact_signal = {
            "gateway_fee": _fee_fully_explains_shortfall(exception),
            "refund": _refund_fully_explains_shortfall(exception),
            "timing_difference": "settlement_outside_window" in exception.exception_reasons,
            "duplicate_or_missing_transaction": _has_duplicate_or_missing_source(exception),
            "manual_adjustment": _has_unexplained_one_to_one_amount_mismatch(exception),
            "unknown_other": False,
        }[hypothesis]
        return (0 if exact_signal else 1, 0 if hypothesis in historical_hypotheses else 1)

    taxonomy_index = {hypothesis: index for index, hypothesis in enumerate(HYPOTHESIS_TAXONOMY)}
    return sorted(live, key=lambda hypothesis: (*rank(hypothesis), taxonomy_index[hypothesis]))


def _historical_hypotheses(cases: Sequence[HistoricalCase | Mapping[str, object]]) -> set[Hypothesis]:
    hypotheses: set[Hypothesis] = set()
    for case in cases:
        value = case.hypothesis if isinstance(case, HistoricalCase) else case.get("hypothesis") or case.get("root_cause") or case.get("pattern")
        if isinstance(value, str) and value in HYPOTHESIS_TAXONOMY:
            hypotheses.add(value)
    return hypotheses


def _has_recorded_fee(exception: ReconciliationRecord) -> bool:
    gateway = _single(exception.gateway)
    return bool(gateway and gateway.fee and gateway.fee > Decimal("0.00"))


def _has_recorded_refund(exception: ReconciliationRecord) -> bool:
    gateway = _single(exception.gateway)
    return bool(gateway and gateway.refund_amount and gateway.refund_amount > Decimal("0.00"))


def _has_bank_shortfall(exception: ReconciliationRecord) -> bool:
    gross = _gross_amount(exception)
    bank_amount = _bank_amount(exception)
    return gross is not None and bank_amount is not None and gross > bank_amount


def _fee_fully_explains_shortfall(exception: ReconciliationRecord) -> bool:
    gateway = _single(exception.gateway)
    return bool(gateway and gateway.fee and _amount_explains_shortfall(exception, gateway.fee))


def _refund_fully_explains_shortfall(exception: ReconciliationRecord) -> bool:
    gateway = _single(exception.gateway)
    return bool(gateway and gateway.refund_amount and _amount_explains_shortfall(exception, gateway.refund_amount))


def _amount_explains_shortfall(exception: ReconciliationRecord, amount: Decimal) -> bool:
    gross = _gross_amount(exception)
    bank_amount = _bank_amount(exception)
    if gross is None or bank_amount is None:
        return False
    return (gross - bank_amount).copy_abs().quantize(Decimal("0.01")) == amount.copy_abs().quantize(Decimal("0.01"))


def _gross_amount(exception: ReconciliationRecord) -> Decimal | None:
    gateway = _single(exception.gateway)
    erp = _single(exception.erp)
    if gateway and erp and gateway.amount != erp.amount:
        return None
    return gateway.amount if gateway else erp.amount if erp else None


def _bank_amount(exception: ReconciliationRecord) -> Decimal | None:
    bank = _single(exception.bank)
    if not bank:
        return None
    return bank.settled_amount or bank.amount


def _has_missing_bank_record(exception: ReconciliationRecord) -> bool:
    return len(exception.gateway) == 1 and len(exception.bank) == 0


def _has_duplicate_or_missing_source(exception: ReconciliationRecord) -> bool:
    if len(exception.erp) != 1 or len(exception.gateway) != 1 or len(exception.bank) != 1:
        return True
    return any(match.status in {"UNMATCHED", "AMBIGUOUS"} for match in exception.pairwise)


def _has_unexplained_one_to_one_amount_mismatch(exception: ReconciliationRecord) -> bool:
    if _has_duplicate_or_missing_source(exception):
        return False
    if _fee_fully_explains_shortfall(exception) or _refund_fully_explains_shortfall(exception):
        return False
    return any(match.status == "MISMATCH" for match in exception.pairwise)


def _single(records: Iterable[NormalizedRecord]) -> NormalizedRecord | None:
    records = list(records)
    if len(records) != 1:
        return None
    return records[0]
