"""Shared schemas and constants for the reconciliation investigator."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


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

HYPOTHESIS_TOOL_MAP: dict[Hypothesis, tuple[str, ...]] = {
    "gateway_fee": (
        "get_gateway_transaction",
        "get_fee_configuration",
        "get_bank_settlement",
        "get_refund_record",
    ),
    "refund": (
        "get_gateway_transaction",
        "get_refund_record",
        "get_bank_settlement",
    ),
    "timing_difference": ("get_gateway_transaction", "get_bank_settlement"),
    "manual_adjustment": ("get_erp_transaction", "get_related_transactions"),
    "duplicate_or_missing_transaction": ("get_related_transactions",),
    "unknown_other": ("get_related_transactions",),
}


@dataclass(frozen=True)
class HistoricalCase:
    case_id: str
    hypothesis: Hypothesis
    summary: str = ""
    confidence: Decimal | None = None

