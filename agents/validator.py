from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Literal, Mapping, Sequence


Hypothesis = Literal[
    "gateway_fee",
    "refund",
    "timing_difference",
    "manual_adjustment",
    "duplicate_or_missing_transaction",
    "unknown",
]
Conclusion = Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "REJECTED", "INSUFFICIENT_EVIDENCE"]
EvidenceStatus = Literal["FOUND", "NOT_FOUND", "SOURCE_UNAVAILABLE", "MALFORMED"]

CONTROLLED_HYPOTHESES: tuple[Hypothesis, ...] = (
    "gateway_fee",
    "refund",
    "timing_difference",
    "manual_adjustment",
    "duplicate_or_missing_transaction",
    "unknown",
)

MONEY = Decimal("0.01")


@dataclass(frozen=True)
class EvidenceBundle:
    hypothesis: Hypothesis
    evidence: list[dict[str, Any]]


@dataclass(frozen=True)
class HypothesisResult:
    hypothesis: Hypothesis
    evidence_for: list[dict[str, Any]] = field(default_factory=list)
    evidence_against: list[dict[str, Any]] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    calculations: list[str] = field(default_factory=list)
    conclusion: Conclusion = "INSUFFICIENT_EVIDENCE"
    confidence: Decimal = Decimal("0.00")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confidence"] = float(self.confidence)
        return payload


def validate_hypotheses(
    evidence_bundles: Mapping[str, Sequence[Mapping[str, Any]]] | Sequence[EvidenceBundle | Mapping[str, Any]],
    hypotheses: Iterable[str] | None = None,
    *,
    amount_tolerance: Decimal = MONEY,
) -> list[HypothesisResult]:
    """Validate all requested hypotheses against the full evidence set at once."""
    bundles = _normalize_bundles(evidence_bundles)
    requested = tuple(_controlled_hypotheses(hypotheses or bundles))
    all_evidence = [fact for bundle in bundles.values() for fact in bundle]
    context = _EvidenceContext(all_evidence, amount_tolerance)
    raw_results = {
        "gateway_fee": _validate_gateway_fee(context),
        "refund": _validate_refund(context),
        "timing_difference": _validate_timing_difference(context),
        "manual_adjustment": _validate_manual_adjustment(context),
        "duplicate_or_missing_transaction": _validate_duplicate_or_missing(context),
    }

    supported = any(result.conclusion == "SUPPORTED" for result in raw_results.values())
    if supported:
        unknown = HypothesisResult(
            "unknown",
            evidence_against=_compact_evidence_for_supported(raw_results.values()),
            reasoning=["A supported controlled hypothesis explains the discrepancy, so unknown is rejected."],
            conclusion="REJECTED",
            confidence=Decimal("0.05"),
        )
    else:
        unknown = HypothesisResult(
            "unknown",
            missing_evidence=["No controlled hypothesis has enough supporting evidence to explain the discrepancy."],
            reasoning=["The available evidence does not support a specific configured root cause."],
            conclusion="INSUFFICIENT_EVIDENCE",
            confidence=Decimal("0.40"),
        )
    raw_results["unknown"] = unknown

    return [raw_results[hypothesis] for hypothesis in requested]


def validate(
    evidence_bundles: Mapping[str, Sequence[Mapping[str, Any]]] | Sequence[EvidenceBundle | Mapping[str, Any]],
    hypotheses: Iterable[str] | None = None,
    *,
    amount_tolerance: Decimal = MONEY,
) -> list[HypothesisResult]:
    return validate_hypotheses(evidence_bundles, hypotheses, amount_tolerance=amount_tolerance)


def validator_node(state: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    evidence_bundles = state.get("evidence_bundles", state.get("evidence", {}))
    hypotheses = state.get("hypotheses")
    validations = validate_hypotheses(evidence_bundles, hypotheses)
    return {"validations": [validation.to_dict() for validation in validations]}


class _EvidenceContext:
    def __init__(self, evidence: Sequence[dict[str, Any]], amount_tolerance: Decimal) -> None:
        self.evidence = list(evidence)
        self.amount_tolerance = amount_tolerance

    def facts(self, *names: str) -> list[dict[str, Any]]:
        wanted = {_key(name) for name in names}
        return [fact for fact in self.evidence if _key(str(fact.get("fact", fact.get("name", "")))) in wanted]

    def found(self, *names: str) -> list[dict[str, Any]]:
        return [fact for fact in self.facts(*names) if _status(fact) == "FOUND"]

    def not_found(self, *names: str) -> list[dict[str, Any]]:
        return [fact for fact in self.facts(*names) if _status(fact) == "NOT_FOUND"]

    def unavailable(self, *names: str) -> list[dict[str, Any]]:
        return [fact for fact in self.facts(*names) if _status(fact) in {"SOURCE_UNAVAILABLE", "MALFORMED"}]

    def amount(self, *keys: str) -> Decimal | None:
        for fact in self.evidence:
            amount = _decimal_from_fact(fact, keys)
            if amount is not None:
                return amount
        return None

    def difference(self) -> Decimal | None:
        explicit = self.amount("settlement_difference", "difference", "shortfall")
        if explicit is not None:
            return explicit.copy_abs().quantize(MONEY)
        gross = self.amount("expected_amount", "gateway_amount", "gross_amount", "amount")
        settled = self.amount("bank_amount", "settled_amount", "settlement_amount")
        if gross is None or settled is None:
            return None
        return (gross - settled).copy_abs().quantize(MONEY)

    def explains_settlement(self, adjustment: Decimal | None) -> bool:
        gross = self.amount("expected_amount", "gateway_amount", "gross_amount", "amount")
        settled = self.amount("bank_amount", "settled_amount", "settlement_amount")
        if gross is None or settled is None or adjustment is None:
            return False
        return abs((gross - adjustment) - settled) <= self.amount_tolerance


def _validate_gateway_fee(context: _EvidenceContext) -> HypothesisResult:
    evidence_for: list[dict[str, Any]] = []
    evidence_against: list[dict[str, Any]] = []
    missing: list[str] = []
    reasoning: list[str] = []
    calculations: list[str] = []

    fee = context.amount("gateway_fee", "fee")
    fee_rate = context.amount("fee_rate", "rate")
    gross = context.amount("expected_amount", "gateway_amount", "gross_amount", "amount")
    bank = context.amount("bank_amount", "settled_amount", "settlement_amount")

    for fact in context.found("gateway_fee", "gateway_transaction", "fee_configuration", "bank_settlement", "historical_cases"):
        evidence_for.append(fact)
    evidence_against.extend(context.found("refund_record"))

    if fee is None:
        missing.append("Gateway fee evidence is missing.")
    elif fee <= Decimal("0.00"):
        evidence_against.extend(context.facts("gateway_fee", "gateway_transaction"))
        reasoning.append("The gateway does not record a positive processing fee.")

    if gross is None:
        missing.append("Gross ERP or gateway amount is missing.")
    if bank is None:
        missing.append("Bank settlement amount is missing.")
    if fee_rate is None:
        missing.append("Fee rate evidence is missing.")

    rate_matches_fee = False
    if gross is not None and fee is not None and fee_rate is not None:
        calculated_fee = (gross * fee_rate).quantize(MONEY)
        calculations.append(f"{_fmt_money(gross)} * {_fmt_rate(fee_rate)} = {_fmt_money(calculated_fee)}")
        reasoning.append(
            f"{_fmt_grouped(gross)} × {_fmt_percent(fee_rate)} = {_fmt_grouped(calculated_fee)}, matching the recorded gateway fee."
        )
        rate_matches_fee = abs(calculated_fee - fee) <= context.amount_tolerance
        if not rate_matches_fee:
            evidence_against.extend(context.facts("fee_configuration", "gateway_transaction"))
            reasoning.append("The recorded fee rate does not reproduce the gateway fee amount.")

    settlement_matches_fee = context.explains_settlement(fee)
    if gross is not None and fee is not None and bank is not None:
        settled_after_fee = (gross - fee).quantize(MONEY)
        calculations.append(f"{_fmt_money(gross)} - {_fmt_money(fee)} = {_fmt_money(settled_after_fee)}")
        reasoning.append(
            f"{_fmt_grouped(gross)} - {_fmt_grouped(fee)} = {_fmt_grouped(settled_after_fee)}, which matches the bank settlement of {_fmt_grouped(bank)}."
        )
        if not settlement_matches_fee:
            evidence_against.extend(context.facts("bank_settlement"))
            reasoning.append("Gross amount minus gateway fee does not match the settled bank amount.")

    if context.unavailable("refund_record"):
        missing.append("Refund source was unavailable, so absence of refund cannot be used as proof.")
    elif context.not_found("refund_record"):
        reasoning.append("Refund evidence was checked and no refund record was found.")
        evidence_for.extend(context.not_found("refund_record"))

    if fee and fee > 0 and rate_matches_fee and settlement_matches_fee and not context.unavailable("refund_record"):
        conclusion: Conclusion = "SUPPORTED"
        confidence = Decimal("0.96")
        reasoning.append("The recorded processing fee explains the full settlement shortfall.")
    elif evidence_for and not evidence_against:
        conclusion = "PARTIALLY_SUPPORTED"
        confidence = Decimal("0.65")
        reasoning.append("Some fee evidence exists, but required arithmetic or source checks are incomplete.")
    elif missing and not evidence_against:
        conclusion = "INSUFFICIENT_EVIDENCE"
        confidence = Decimal("0.30")
        reasoning.append("Gateway fee cannot be validated because required evidence is missing.")
    else:
        conclusion = "REJECTED"
        confidence = Decimal("0.10")
        if not reasoning:
            reasoning.append("Gateway fee evidence does not explain the discrepancy.")

    return HypothesisResult("gateway_fee", evidence_for, evidence_against, missing, reasoning, calculations, conclusion, confidence)


def _validate_refund(context: _EvidenceContext) -> HypothesisResult:
    refund = context.amount("refund_amount", "refund")
    evidence_for = context.found("refund_record")
    evidence_against: list[dict[str, Any]] = []
    missing: list[str] = []
    reasoning: list[str] = []
    calculations: list[str] = []

    if context.unavailable("refund_record"):
        missing.append("Refund source was unavailable.")
        reasoning.append("Refund cannot be ruled in or out because refund evidence was unavailable.")
        return HypothesisResult("refund", evidence_for, evidence_against, missing, reasoning, calculations, "INSUFFICIENT_EVIDENCE", Decimal("0.25"))

    if context.not_found("refund_record"):
        evidence_against.extend(context.not_found("refund_record"))
        reasoning.append("Refund is rejected because no refund record was found.")
    elif refund is None or refund <= Decimal("0.00"):
        evidence_against.extend(context.facts("refund_record", "gateway_transaction"))
        reasoning.append("Refund is rejected because the available records do not show a positive refund amount.")
    else:
        gross = context.amount("expected_amount", "gateway_amount", "gross_amount", "amount")
        bank = context.amount("bank_amount", "settled_amount", "settlement_amount")
        if gross is not None and bank is not None:
            expected = (gross - refund).quantize(MONEY)
            calculations.append(f"{_fmt_money(gross)} - {_fmt_money(refund)} = {_fmt_money(expected)}")
            if context.explains_settlement(refund):
                reasoning.append("Refund is supported because gross amount minus refund matches the bank settlement.")
                return HypothesisResult("refund", evidence_for, evidence_against, missing, reasoning, calculations, "SUPPORTED", Decimal("0.92"))
            evidence_against.extend(context.facts("bank_settlement"))
            reasoning.append("Refund is rejected because gross amount minus refund does not match the bank settlement.")
        else:
            missing.append("Gross amount or bank settlement is missing.")
            reasoning.append("Refund has some evidence, but settlement arithmetic cannot be checked.")
            return HypothesisResult("refund", evidence_for, evidence_against, missing, reasoning, calculations, "PARTIALLY_SUPPORTED", Decimal("0.60"))

    return HypothesisResult("refund", evidence_for, evidence_against, missing, reasoning, calculations, "REJECTED", Decimal("0.08"))


def _validate_timing_difference(context: _EvidenceContext) -> HypothesisResult:
    evidence_for = context.found("settlement_delay", "pending_settlement")
    evidence_against = context.found("bank_settlement")
    if evidence_for and not evidence_against:
        return HypothesisResult(
            "timing_difference",
            evidence_for=evidence_for,
            reasoning=["Timing difference is supported because settlement is still pending within the expected window."],
            conclusion="SUPPORTED",
            confidence=Decimal("0.85"),
        )
    return HypothesisResult(
        "timing_difference",
        evidence_against=evidence_against,
        reasoning=["Timing difference is rejected because a bank settlement record is already present."],
        conclusion="REJECTED",
        confidence=Decimal("0.10"),
    )


def _validate_manual_adjustment(context: _EvidenceContext) -> HypothesisResult:
    evidence_for = context.found("manual_adjustment")
    if evidence_for:
        return HypothesisResult(
            "manual_adjustment",
            evidence_for=evidence_for,
            reasoning=["Manual adjustment is supported by an explicit adjustment record."],
            conclusion="SUPPORTED",
            confidence=Decimal("0.88"),
        )
    return HypothesisResult(
        "manual_adjustment",
        evidence_against=context.not_found("manual_adjustment"),
        reasoning=["Manual adjustment is rejected because no adjustment record was found in the evidence bundle."],
        conclusion="REJECTED",
        confidence=Decimal("0.08"),
    )


def _validate_duplicate_or_missing(context: _EvidenceContext) -> HypothesisResult:
    evidence_for = context.found("duplicate_transaction", "missing_transaction", "related_transactions")
    if evidence_for and any(_truthy_marker(fact) for fact in evidence_for):
        return HypothesisResult(
            "duplicate_or_missing_transaction",
            evidence_for=evidence_for,
            reasoning=["Duplicate or missing transaction is supported by related-transaction evidence."],
            conclusion="SUPPORTED",
            confidence=Decimal("0.86"),
        )
    return HypothesisResult(
        "duplicate_or_missing_transaction",
        evidence_against=context.not_found("duplicate_transaction", "missing_transaction", "related_transactions"),
        reasoning=["Duplicate or missing transaction is rejected because related-transaction evidence found no duplicate or missing record."],
        conclusion="REJECTED",
        confidence=Decimal("0.08"),
    )


def _normalize_bundles(
    evidence_bundles: Mapping[str, Sequence[Mapping[str, Any]]] | Sequence[EvidenceBundle | Mapping[str, Any]],
) -> dict[Hypothesis, list[dict[str, Any]]]:
    normalized: dict[Hypothesis, list[dict[str, Any]]] = {hypothesis: [] for hypothesis in CONTROLLED_HYPOTHESES}
    if isinstance(evidence_bundles, Mapping):
        items = evidence_bundles.items()
        for hypothesis, evidence in items:
            normalized[_controlled_hypothesis(hypothesis)].extend(_normalize_facts(evidence))
        return normalized

    for bundle in evidence_bundles:
        if isinstance(bundle, EvidenceBundle):
            normalized[bundle.hypothesis].extend(_normalize_facts(bundle.evidence))
            continue
        hypothesis = _controlled_hypothesis(str(bundle.get("hypothesis", "")))
        evidence = bundle.get("evidence", bundle.get("facts", []))
        normalized[hypothesis].extend(_normalize_facts(evidence))
    return normalized


def _normalize_facts(evidence: Any) -> list[dict[str, Any]]:
    if evidence is None:
        return []
    facts = evidence if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, Mapping)) else [evidence]
    normalized: list[dict[str, Any]] = []
    for fact in facts:
        if is_dataclass(fact):
            normalized.append(asdict(fact))
        elif isinstance(fact, Mapping):
            normalized.append(dict(fact))
        else:
            normalized.append({"fact": str(fact), "value": fact, "status": "FOUND"})
    return normalized


def _controlled_hypotheses(hypotheses: Iterable[str] | Mapping[str, Any]) -> list[Hypothesis]:
    return [_controlled_hypothesis(hypothesis) for hypothesis in hypotheses]


def _controlled_hypothesis(hypothesis: str) -> Hypothesis:
    if hypothesis not in CONTROLLED_HYPOTHESES:
        raise ValueError(f"Unsupported hypothesis: {hypothesis}")
    return hypothesis  # type: ignore[return-value]


def _compact_evidence_for_supported(results: Iterable[HypothesisResult]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for result in results:
        if result.conclusion == "SUPPORTED":
            compact.append({"fact": f"{result.hypothesis}_supported", "value": result.reasoning[-1], "status": "FOUND"})
    return compact


def _key(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _status(fact: Mapping[str, Any]) -> str:
    return str(fact.get("status", "FOUND")).strip().upper()


def _decimal_from_fact(fact: Mapping[str, Any], keys: Sequence[str]) -> Decimal | None:
    wanted = {_key(key) for key in keys}
    candidates: list[Any] = []
    for key, value in fact.items():
        if _key(str(key)) in wanted:
            candidates.append(value)
    value = fact.get("value")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _key(str(key)) in wanted:
                candidates.append(nested)
    elif _key(str(fact.get("fact", fact.get("name", "")))) in wanted:
        candidates.append(value)

    for candidate in candidates:
        parsed = _to_decimal(candidate)
        if parsed is not None:
            return parsed
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value.quantize(MONEY) if value.as_tuple().exponent < -2 else value
    if isinstance(value, int):
        return Decimal(value).quantize(MONEY)
    try:
        text = str(value).strip().replace(",", "").replace("₹", "")
        if text.endswith("%"):
            return Decimal(text[:-1]) / Decimal("100")
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _fmt_money(value: Decimal) -> str:
    return str(value.quantize(MONEY))


def _fmt_grouped(value: Decimal) -> str:
    quantized = value.quantize(MONEY)
    if quantized == quantized.to_integral():
        return f"{int(quantized):,}"
    return f"{quantized:,.2f}"


def _fmt_rate(value: Decimal) -> str:
    return str(value.normalize())


def _fmt_percent(value: Decimal) -> str:
    percent = value * Decimal("100")
    if percent == percent.to_integral():
        return f"{int(percent)}%"
    return f"{percent.normalize()}%"


def _truthy_marker(fact: Mapping[str, Any]) -> bool:
    value = fact.get("value")
    if isinstance(value, Mapping):
        for key in ("has_duplicate", "has_missing", "duplicate_count", "missing_count"):
            if bool(value.get(key)):
                return True
        return False
    return bool(value)
