from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Literal, Mapping, Sequence

from core.matcher import ReconciliationRecord
from core.normalizer import NormalizedRecord


Hypothesis = Literal[
    "gateway_fee",
    "refund",
    "timing_difference",
    "manual_adjustment",
    "duplicate_or_missing_transaction",
    "unknown_other",
]

HYPOTHESIS_TAXONOMY: tuple[Hypothesis, ...] = (
    "gateway_fee",
    "refund",
    "timing_difference",
    "manual_adjustment",
    "duplicate_or_missing_transaction",
    "unknown_other",
)


@dataclass(frozen=True)
class HistoricalCase:
    case_id: str
    hypothesis: Hypothesis
    summary: str = ""
    confidence: Decimal | None = None


@dataclass(frozen=True)
class InvestigationPlan:
    invoice_id: str
    live_hypotheses: list[Hypothesis]


def plan_investigation(
    exception: ReconciliationRecord,
    historical_cases: Sequence[HistoricalCase | Mapping[str, object]] | None = None,
) -> InvestigationPlan:
    """Return the bounded hypotheses still worth investigating for an exception.

    The planner is intentionally deterministic for the MVP: source matching and
    arithmetic are already controlled by the core reconciliation layer, so this
    function only prunes hypotheses that are clearly inconsistent with the known
    record shape and amounts.
    """

    historical_hypotheses = _historical_hypotheses(historical_cases or [])
    live: list[Hypothesis] = []

    for hypothesis in HYPOTHESIS_TAXONOMY:
        if hypothesis == "unknown_other":
            continue
        if _is_live(hypothesis, exception, historical_hypotheses):
            live.append(hypothesis)

    live = _ordered_by_signal(live, exception, historical_hypotheses)
    live.append("unknown_other")
    return InvestigationPlan(invoice_id=exception.invoice_id, live_hypotheses=live)


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

