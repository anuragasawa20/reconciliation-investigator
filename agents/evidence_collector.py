"""Deterministic hypothesis-directed evidence retrieval.

Results are preserved verbatim, including NOT_FOUND and SOURCE_UNAVAILABLE.
Interpretation and financial calculations belong to downstream nodes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import tools


HYPOTHESIS_TOOL_MAP: dict[str, tuple[str, ...]] = {
    "gateway_fee": (
        "get_gateway_transaction", "get_fee_configuration", "get_bank_settlement",
        "get_refund_record",
    ),
    "refund": (
        "get_gateway_transaction", "get_refund_record", "get_bank_settlement",
    ),
    "timing_difference": ("get_gateway_transaction", "get_bank_settlement"),
    "manual_adjustment": ("get_erp_transaction", "get_related_transactions"),
    "duplicate_or_missing_transaction": ("get_related_transactions",),
    "unknown_other": ("get_related_transactions",),
}


def collect_evidence(
    live_hypotheses: Sequence[str], invoice_id: str,
) -> dict[str, dict[str, Any]]:
    """Call each hypothesis's mapped tools with the invoice ID and bundle results.

    Duplicate hypotheses are collected once, in first-occurrence order. Unknown
    hypotheses and missing invoice IDs fail before any tool runs. Tool failures
    propagate rather than being misrepresented as missing financial records.
    """
    if isinstance(live_hypotheses, (str, bytes)):
        raise TypeError("live_hypotheses must be a sequence of hypothesis names")
    hypotheses = list(live_hypotheses)
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, str) or hypothesis not in HYPOTHESIS_TOOL_MAP:
            raise ValueError(f"Unknown hypothesis: {hypothesis!r}")
    if not isinstance(invoice_id, str) or not invoice_id.strip():
        raise ValueError("invoice_id must be a non-empty string")

    evidence_bundles: dict[str, dict[str, Any]] = {}
    for hypothesis in dict.fromkeys(hypotheses):
        evidence_bundles[hypothesis] = {
            name: getattr(tools, name)(invoice_id=invoice_id)
            for name in HYPOTHESIS_TOOL_MAP[hypothesis]
        }
    return evidence_bundles


def evidence_collector(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a graph state update without mutating the input state."""
    return {
        "evidence_bundles": collect_evidence(
            state["live_hypotheses"], state["invoice_id"],
        ),
    }
