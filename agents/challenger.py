from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Literal, Mapping, Sequence

from agents.planner import HistoricalCase, Hypothesis
from core.matcher import MatchConfig, ReconciliationRecord
from core.normalizer import NormalizedRecord


ValidationConclusion = Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "NOT_SUPPORTED", "INSUFFICIENT_EVIDENCE"]


@dataclass(frozen=True)
class HypothesisValidation:
    hypothesis: Hypothesis
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    calculations: list[str] = field(default_factory=list)
    conclusion: ValidationConclusion = "INSUFFICIENT_EVIDENCE"
    confidence: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class ChallengeCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ChallengeResult:
    hypothesis: Hypothesis | None
    checks_performed: list[ChallengeCheck]
    contradictory_evidence: list[str]
    unresolved_questions: list[str]
    challenge_notes: list[str]
    adjusted_confidence: Decimal
    force_human_review: bool
    final_explanation: str | None


def challenge_hypotheses(
    exception: ReconciliationRecord,
    validations: Sequence[HypothesisValidation | Mapping[str, object]],
    historical_cases: Sequence[HistoricalCase | Mapping[str, object]] | None = None,
    config: MatchConfig | None = None,
) -> ChallengeResult:
    """Challenge the leading supported hypothesis as a separate investigation step."""

    config = config or MatchConfig()
    normalized = [_coerce_validation(result) for result in validations]
    leading = _leading_supported(normalized)
    notes = _unwarranted_rejection_notes(normalized)

    if leading is None:
        return ChallengeResult(
            hypothesis=None,
            checks_performed=[],
            contradictory_evidence=[],
            unresolved_questions=["No supported hypothesis was available for challenge."],
            challenge_notes=notes,
            adjusted_confidence=Decimal("0.00"),
            force_human_review=True,
            final_explanation=None,
        )

    if leading.hypothesis == "gateway_fee":
        return _challenge_gateway_fee(exception, leading, notes, historical_cases or [], config)

    checks, contradictions, unresolved = _challenge_generic(exception, leading)
    adjusted = _adjust_confidence(leading.confidence, contradictions, unresolved, notes)
    force_review = bool(contradictions or adjusted < Decimal("0.70"))
    explanation = None if force_review else _generic_explanation(exception, leading, checks)
    return ChallengeResult(
        hypothesis=leading.hypothesis,
        checks_performed=checks,
        contradictory_evidence=contradictions,
        unresolved_questions=unresolved,
        challenge_notes=notes,
        adjusted_confidence=adjusted,
        force_human_review=force_review,
        final_explanation=explanation,
    )


def _challenge_gateway_fee(
    exception: ReconciliationRecord,
    leading: HypothesisValidation,
    notes: list[str],
    historical_cases: Sequence[HistoricalCase | Mapping[str, object]],
    config: MatchConfig,
) -> ChallengeResult:
    checks: list[ChallengeCheck] = []
    contradictions: list[str] = []
    unresolved: list[str] = []

    erp = _single(exception.erp)
    gateway = _single(exception.gateway)
    bank = _single(exception.bank)
    gross = _gross_amount(erp, gateway)
    settled = _bank_amount(bank)

    if gateway is None:
        unresolved.append("Gateway source record is missing or duplicated.")
        checks.append(ChallengeCheck("fee_recorded", False, "No single gateway record is available."))
    elif gateway.fee is None or gateway.fee <= Decimal("0.00"):
        contradictions.append("Gateway record does not contain a positive processing fee.")
        checks.append(ChallengeCheck("fee_recorded", False, f"{gateway.transaction_id} has no positive fee."))
    else:
        checks.append(ChallengeCheck("fee_recorded", True, f"{gateway.transaction_id} records fee {gateway.fee}."))

    if gateway and gateway.fee and gateway.fee_rate is not None:
        expected_fee = _money(gateway.amount * gateway.fee_rate)
        if _within_tolerance(expected_fee, gateway.fee, config.amount_tolerance):
            checks.append(
                ChallengeCheck(
                    "fee_rate_reproduces_fee",
                    True,
                    f"{gateway.amount} * {gateway.fee_rate} = {expected_fee}.",
                )
            )
        else:
            contradictions.append(
                f"Configured fee rate gives {expected_fee}, not recorded fee {gateway.fee}."
            )
            checks.append(
                ChallengeCheck(
                    "fee_rate_reproduces_fee",
                    False,
                    f"{gateway.amount} * {gateway.fee_rate} = {expected_fee}.",
                )
            )
    elif gateway and gateway.fee:
        unresolved.append("No fee rate is available to reproduce the recorded fee.")

    if gross is None or gateway is None or not gateway.fee or settled is None:
        unresolved.append("Gross amount, fee, and bank settlement are all required to test net settlement.")
    else:
        expected_settlement = _money(gross - gateway.fee)
        if _within_tolerance(expected_settlement, settled, config.amount_tolerance):
            checks.append(
                ChallengeCheck(
                    "gross_minus_fee_equals_settlement",
                    True,
                    f"{gross} - {gateway.fee} = {expected_settlement}; bank settled {settled}.",
                )
            )
        else:
            contradictions.append(
                f"Gross minus fee equals {expected_settlement}, but bank settled {settled}."
            )
            checks.append(
                ChallengeCheck(
                    "gross_minus_fee_equals_settlement",
                    False,
                    f"{gross} - {gateway.fee} = {expected_settlement}; bank settled {settled}.",
                )
            )

    if gateway and gateway.refund_amount and gateway.refund_amount > Decimal("0.00"):
        refund_detail = f"Gateway refund amount {gateway.refund_amount} is present on {gateway.transaction_id}."
        contradictions.append(refund_detail)
        checks.append(ChallengeCheck("refund_not_explaining_difference", False, refund_detail))
    elif gateway:
        checks.append(ChallengeCheck("refund_not_explaining_difference", True, "Gateway refund amount is zero."))

    if "settlement_outside_window" in exception.exception_reasons:
        contradictions.append("Settlement is outside the configured normal window.")
        checks.append(ChallengeCheck("settlement_inside_window", False, "Settlement window warning is present."))
    else:
        checks.append(ChallengeCheck("settlement_inside_window", True, "No settlement window warning is present."))

    if len(exception.erp) == 1 and len(exception.gateway) == 1 and len(exception.bank) == 1:
        checks.append(ChallengeCheck("no_duplicate_or_missing_records", True, "Each source has exactly one record."))
    else:
        contradictions.append("One or more sources has missing or duplicate records.")
        checks.append(
            ChallengeCheck(
                "no_duplicate_or_missing_records",
                False,
                f"ERP={len(exception.erp)}, gateway={len(exception.gateway)}, bank={len(exception.bank)}.",
            )
        )

    history_contradiction = _history_contradiction("gateway_fee", historical_cases)
    if history_contradiction:
        contradictions.append(history_contradiction)
    elif historical_cases:
        checks.append(ChallengeCheck("history_not_contradictory", True, "Historical cases do not contradict gateway_fee."))

    adjusted = _adjust_confidence(leading.confidence, contradictions, unresolved, notes)
    force_review = bool(contradictions or adjusted < Decimal("0.70"))
    explanation = None if force_review else _gateway_fee_explanation(exception, checks)

    return ChallengeResult(
        hypothesis=leading.hypothesis,
        checks_performed=checks,
        contradictory_evidence=contradictions,
        unresolved_questions=unresolved,
        challenge_notes=notes,
        adjusted_confidence=adjusted,
        force_human_review=force_review,
        final_explanation=explanation,
    )


def _challenge_generic(
    exception: ReconciliationRecord,
    leading: HypothesisValidation,
) -> tuple[list[ChallengeCheck], list[str], list[str]]:
    checks = [
        ChallengeCheck(
            "supported_by_validator",
            True,
            f"Validator marked {leading.hypothesis} as SUPPORTED at {leading.confidence}.",
        )
    ]
    contradictions: list[str] = []
    unresolved: list[str] = []
    if len(exception.erp) != 1 or len(exception.gateway) != 1 or len(exception.bank) != 1:
        unresolved.append("A complete one-to-one source record set is not available.")
    return checks, contradictions, unresolved


def _leading_supported(validations: Sequence[HypothesisValidation]) -> HypothesisValidation | None:
    supported = [result for result in validations if result.conclusion == "SUPPORTED"]
    if not supported:
        return None
    return sorted(supported, key=lambda result: result.confidence, reverse=True)[0]


def _unwarranted_rejection_notes(validations: Sequence[HypothesisValidation]) -> list[str]:
    notes: list[str] = []
    for result in validations:
        if result.conclusion != "NOT_SUPPORTED":
            continue
        if result.evidence_against:
            continue
        notes.append(
            f"Validator rejected {result.hypothesis} without contradictory evidence; "
            "keep it visible for human review instead of treating it as ruled out."
        )
    return notes


def _coerce_validation(value: HypothesisValidation | Mapping[str, object]) -> HypothesisValidation:
    if isinstance(value, HypothesisValidation):
        return value
    hypothesis = value.get("hypothesis")
    conclusion = value.get("conclusion", "INSUFFICIENT_EVIDENCE")
    confidence = value.get("confidence", Decimal("0.00"))
    if hypothesis not in {"gateway_fee", "refund", "timing_difference", "manual_adjustment", "duplicate_or_missing_transaction", "unknown_other"}:
        raise ValueError(f"Unknown hypothesis: {hypothesis}")
    if conclusion not in {"SUPPORTED", "PARTIALLY_SUPPORTED", "NOT_SUPPORTED", "INSUFFICIENT_EVIDENCE"}:
        raise ValueError(f"Unknown validation conclusion: {conclusion}")
    return HypothesisValidation(
        hypothesis=hypothesis,  # type: ignore[arg-type]
        evidence_for=_string_list(value.get("evidence_for", [])),
        evidence_against=_string_list(value.get("evidence_against", [])),
        missing_evidence=_string_list(value.get("missing_evidence", [])),
        reasoning=_string_list(value.get("reasoning", [])),
        calculations=_string_list(value.get("calculations", [])),
        conclusion=conclusion,  # type: ignore[arg-type]
        confidence=_decimal(confidence),
    )


def _history_contradiction(
    hypothesis: Hypothesis,
    historical_cases: Sequence[HistoricalCase | Mapping[str, object]],
) -> str | None:
    strong_other_cases = []
    for case in historical_cases:
        case_hypothesis = case.hypothesis if isinstance(case, HistoricalCase) else case.get("hypothesis") or case.get("pattern")
        confidence = case.confidence if isinstance(case, HistoricalCase) else case.get("confidence")
        if case_hypothesis != hypothesis and _decimal(confidence or Decimal("0.00")) >= Decimal("0.90"):
            strong_other_cases.append(str(case_hypothesis))
    if strong_other_cases:
        return f"Historical evidence strongly points to {', '.join(sorted(set(strong_other_cases)))} instead."
    return None


def _gateway_fee_explanation(exception: ReconciliationRecord, checks: Sequence[ChallengeCheck]) -> str:
    gateway = _single(exception.gateway)
    bank = _single(exception.bank)
    erp = _single(exception.erp)
    gross = _gross_amount(erp, gateway)
    settled = _bank_amount(bank)
    if gateway is None or gross is None or gateway.fee is None or settled is None:
        return "The gateway fee hypothesis survived challenge, but the explanation is incomplete."
    rate_text = f" at rate {gateway.fee_rate}" if gateway.fee_rate is not None else ""
    passed = [check.detail for check in checks if check.passed]
    support_text = " ".join(passed)
    return (
        f"Gateway fee is confirmed for {exception.invoice_id}: ERP and gateway gross amount is {gross}; "
        f"the gateway records fee {gateway.fee}{rate_text}; bank settled {settled}. "
        f"The deterministic settlement calculation is {gross} - {gateway.fee} = {_money(gross - gateway.fee)}. "
        f"{support_text}"
    )


def _generic_explanation(
    exception: ReconciliationRecord,
    leading: HypothesisValidation,
    checks: Sequence[ChallengeCheck],
) -> str:
    check_details = " ".join(check.detail for check in checks if check.passed)
    return f"{leading.hypothesis} remains supported for {exception.invoice_id} after challenge. {check_details}"


def _adjust_confidence(
    confidence: Decimal,
    contradictions: Sequence[str],
    unresolved: Sequence[str],
    notes: Sequence[str],
) -> Decimal:
    adjusted = confidence
    adjusted -= Decimal("0.20") * len(contradictions)
    adjusted -= Decimal("0.05") * len(unresolved)
    adjusted -= Decimal("0.05") * len(notes)
    return max(Decimal("0.00"), _money(adjusted))


def _gross_amount(erp: NormalizedRecord | None, gateway: NormalizedRecord | None) -> Decimal | None:
    if erp and gateway and erp.amount != gateway.amount:
        return None
    if gateway:
        return gateway.amount
    if erp:
        return erp.amount
    return None


def _bank_amount(bank: NormalizedRecord | None) -> Decimal | None:
    if bank is None:
        return None
    return bank.settled_amount or bank.amount


def _within_tolerance(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    return abs(left - right) <= tolerance


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value]
    return [str(value)]


def _single(records: Iterable[NormalizedRecord]) -> NormalizedRecord | None:
    records = list(records)
    if len(records) != 1:
        return None
    return records[0]
