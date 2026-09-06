"""Deterministic closing narrative for an investigation.

This module only restates structured investigation facts. It never invents
amounts, fees, refunds, or historical cases. Callers must treat it as optional:
``compose_investigation_narrative`` swallows unexpected errors and returns
fallback text so a display or summary failure cannot stop the graph.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

CAUSE_PHRASE = {
    "gateway_fee": "a gateway processing fee",
    "refund": "a customer refund",
    "timing_difference": "a settlement timing difference",
    "manual_adjustment": "a manual adjustment",
    "duplicate_or_missing_transaction": "a duplicate or missing transaction",
    "unknown": "an unknown cause",
    "unknown_other": "an unknown cause",
}

ACTION_PHRASE = {
    "AUTO_RESOLVE": "The recommended action is to auto-resolve this as an expected operational difference, not a posting error.",
    "HUMAN_REVIEW": "The recommended action is human review before any financial posting.",
    "WAIT_FOR_SETTLEMENT": "The recommended action is to wait for the bank settlement rather than treat the gap as a loss.",
    "REQUEST_MISSING_EVIDENCE": "The recommended action is to request the missing evidence rather than guess a cause.",
}

UNKNOWN_CAUSES = frozenset({"unknown", "unknown_other", ""})


def compose_investigation_narrative(values: Mapping[str, Any] | None) -> str:
    """Return a plain-language conclusion, or a short fallback if facts are thin."""

    try:
        payload = dict(values or {})
        paragraphs = [part for part in (_mismatch_paragraph(payload), _cause_paragraph(payload), _close_paragraph(payload)) if part]
        if paragraphs:
            return "\n\n".join(paragraphs)
        recommendation = payload.get("recommendation") or {}
        fallback = str(recommendation.get("reason") or payload.get("case_summary") or "").strip()
        return fallback
    except Exception:
        try:
            recommendation = (values or {}).get("recommendation") or {}
            return str(recommendation.get("reason") or (values or {}).get("case_summary") or "").strip()
        except Exception:
            return ""


def _mismatch_paragraph(values: Mapping[str, Any]) -> str:
    canonical = _canonical(values)
    invoice = str(canonical.get("invoice_id") or _invoice_id(values) or "this invoice")
    currency = str(canonical.get("currency") or "INR")
    expected = _money(canonical.get("expected_amount"), currency)
    gateway = _money(canonical.get("gateway_amount"), currency)
    fee = _money(canonical.get("gateway_fee"), currency)
    settled = _money(canonical.get("bank_amount"), currency)
    difference = _difference_text(canonical, currency)

    erp_clause = f"ERP booked {expected}" if expected else "ERP did not supply a usable booked amount"
    gateway_clause = f"the payment gateway recorded {gateway}" if gateway else "the payment gateway record was missing"
    if settled:
        bank_clause = f"the bank settled {settled}"
    else:
        bank_clause = "no matching bank settlement was found"

    parts = [
        f"Invoice {invoice} does not fully reconcile across the three source systems.",
        f"{erp_clause}, {gateway_clause}, and {bank_clause}.",
    ]
    if difference:
        parts.append(f"The unexplained mismatch is {difference}.")
    elif not settled:
        parts.append("The mismatch is an unmatched or still-pending settlement, not a completed equal match.")
    if fee:
        parts.append(f"The gateway also records a processing fee of {fee}.")
    return " ".join(parts)


def _cause_paragraph(values: Mapping[str, Any]) -> str:
    recommendation = values.get("recommendation") or {}
    root_cause = str(values.get("root_cause") or recommendation.get("resolution") or "unknown")
    leading = _leading_validation(values, root_cause)
    phrase = CAUSE_PHRASE.get(root_cause, _humanize(root_cause))
    if root_cause in UNKNOWN_CAUSES:
        missing = recommendation.get("missing_item")
        missing_text = f" Missing evidence: {missing}." if missing else ""
        return (
            f"The investigator could not assign a supported root cause from the bounded hypothesis library. "
            f"Available ERP, gateway, and bank facts do not uniquely explain the mismatch, so the case is left as unknown.{missing_text}"
        )

    conclusion = str((leading or {}).get("conclusion") or "").replace("_", " ").lower()
    opener = f"The investigation concludes that the mismatch is explained by {phrase}"
    if conclusion:
        opener += f" ({conclusion})"
    opener += "."

    sentences = [opener]
    calculations = list((leading or {}).get("calculations") or [])
    if calculations:
        sentences.append("Deterministic arithmetic supporting this cause: " + "; ".join(str(item) for item in calculations[:4]) + ".")
    reasoning = [str(item).rstrip(".") for item in (leading or {}).get("reasoning") or [] if str(item).strip()]
    if reasoning:
        sentences.append(" ".join(item + "." for item in reasoning[:4]))

    alternatives = _rejected_alternatives(values, root_cause)
    if alternatives:
        sentences.append("Other bounded hypotheses were considered and not selected: " + "; ".join(alternatives) + ".")
    return " ".join(sentences)


def _close_paragraph(values: Mapping[str, Any]) -> str:
    challenge = values.get("challenge") or {}
    recommendation = values.get("recommendation") or {}
    action = str(recommendation.get("action") or "")
    confidence = _percent(values.get("confidence") or challenge.get("adjusted_confidence"))
    contradictions = [str(item) for item in challenge.get("contradictory_evidence") or [] if str(item).strip()]
    unresolved = [str(item) for item in challenge.get("unresolved_questions") or [] if str(item).strip()]
    explanation = str(challenge.get("final_explanation") or "").strip()
    policy_reason = str(recommendation.get("reason") or "").strip()

    parts: list[str] = []
    if explanation:
        parts.append(explanation.rstrip(".") + ".")
    if contradictions:
        parts.append("The independent challenger found contradicting evidence: " + " ".join(contradictions[:3]))
        if not parts[-1].endswith("."):
            parts[-1] += "."
    elif challenge:
        parts.append("The independent challenger did not find a fact that fully falsifies the leading cause.")
    if unresolved:
        parts.append("Unresolved questions remain: " + " ".join(item.rstrip(".") + "." for item in unresolved[:3]))
    if policy_reason:
        parts.append(policy_reason.rstrip(".") + ".")
    action_text = ACTION_PHRASE.get(action)
    if action_text:
        parts.append(action_text)
    if confidence is not None:
        parts.append(f"Confidence on this conclusion is {confidence:.0%}.")
    review = values.get("review") or {}
    if review.get("decision"):
        parts.append(f"A reviewer recorded {_humanize(review.get('decision'))}.")
    return " ".join(part for part in parts if part)


def _canonical(values: Mapping[str, Any]) -> dict[str, Any]:
    exception = values.get("exception")
    if exception is None:
        return {}
    canonical = getattr(exception, "canonical_transaction", None)
    if callable(canonical):
        canonical = exception.canonical_transaction
    if isinstance(canonical, dict):
        return canonical
    if isinstance(exception, dict):
        nested = exception.get("canonical_transaction")
        return nested if isinstance(nested, dict) else exception
    return {}


def _invoice_id(values: Mapping[str, Any]) -> str:
    exception = values.get("exception")
    if exception is None:
        return str(values.get("invoice_id") or "")
    return str(getattr(exception, "invoice_id", None) or values.get("invoice_id") or "")


def _leading_validation(values: Mapping[str, Any], root_cause: str) -> dict[str, Any] | None:
    validations = list(values.get("validations") or [])
    for item in validations:
        if not isinstance(item, dict):
            continue
        if str(item.get("hypothesis") or "") == root_cause:
            return item
    return validations[0] if validations and isinstance(validations[0], dict) else None


def _rejected_alternatives(values: Mapping[str, Any], root_cause: str) -> list[str]:
    lines: list[str] = []
    for item in values.get("validations") or []:
        if not isinstance(item, dict):
            continue
        hypothesis = str(item.get("hypothesis") or "")
        if hypothesis == root_cause or hypothesis in UNKNOWN_CAUSES:
            continue
        conclusion = str(item.get("conclusion") or "not selected").replace("_", " ").lower()
        reason = ""
        reasoning = item.get("reasoning") or []
        if reasoning:
            reason = str(reasoning[0]).rstrip(".")
        against = item.get("evidence_against") or []
        if not reason and against:
            reason = str(against[0]).rstrip(".")
        detail = f"{_humanize(hypothesis)} was {conclusion}"
        if reason:
            detail += f" because {reason[:180]}"
        lines.append(detail)
    return lines[:4]


def _difference_text(canonical: Mapping[str, Any], currency: str) -> str:
    expected = _decimal(canonical.get("expected_amount") or canonical.get("gateway_amount"))
    settled = _decimal(canonical.get("bank_amount"))
    if expected is None or settled is None:
        return ""
    gap = expected - settled
    if gap == 0:
        return ""
    label = "shortfall" if gap > 0 else "over-settlement"
    return f"a {label} of {_money(abs(gap), currency)}"


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _money(value: Any, currency: str = "INR") -> str:
    amount = _decimal(value)
    if amount is None:
        return ""
    quantized = amount.quantize(Decimal("0.01"))
    symbol = "₹" if currency == "INR" else f"{currency} "
    return f"{symbol}{quantized:,.2f}"


def _percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return 0.0
    if number > 1:
        number = number / 100.0 if number <= 100 else 1.0
    return number


def _humanize(value: Any) -> str:
    text = str(value or "not available").replace("_", " ").strip()
    return text.lower()
