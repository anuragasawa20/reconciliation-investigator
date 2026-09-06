"""Deterministic hypothesis-directed evidence retrieval.

Results are preserved verbatim, including NOT_FOUND and SOURCE_UNAVAILABLE.
Interpretation and financial calculations belong to downstream nodes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import tools
from schemas import HYPOTHESIS_TOOL_MAP

ToolProgress = Callable[[str, str, str, dict[str, Any] | None], None]


def collect_evidence(
    live_hypotheses: Sequence[str],
    invoice_id: str,
    *,
    on_result: ToolProgress | None = None,
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
        bundle: dict[str, Any] = {}
        for name in HYPOTHESIS_TOOL_MAP[hypothesis]:
            if on_result is not None:
                on_result("start", hypothesis, name, None)
            result = getattr(tools, name)(invoice_id=invoice_id)
            bundle[name] = result
            if on_result is not None:
                on_result("finish", hypothesis, name, result)
        evidence_bundles[hypothesis] = bundle
    return evidence_bundles


def evidence_collector(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a graph state update without mutating the input state."""
    return {
        "evidence_bundles": collect_evidence(
            state["live_hypotheses"], state["invoice_id"],
        ),
    }
