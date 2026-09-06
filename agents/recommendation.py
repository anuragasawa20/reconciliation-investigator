"""Deterministic recommendation gate for investigation results.

This module is deliberately plain Python. It owns the final action boundary:
auto-resolution is allowed only when the selected hypothesis satisfies every
policy condition from the technical specification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Iterable


class RecommendationAction(str, Enum):
    AUTO_RESOLVE = "AUTO_RESOLVE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    WAIT_FOR_SETTLEMENT = "WAIT_FOR_SETTLEMENT"
    REQUEST_MISSING_EVIDENCE = "REQUEST_MISSING_EVIDENCE"


CURRENT_SOURCE_SYSTEMS = frozenset({"erp", "gateway", "bank"})
UNKNOWN_HYPOTHESES = frozenset({"unknown", "unknown_other"})


def _decimal(value: Decimal | int | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class EvidenceFact:
    fact: str
    source: str
    source_record_id: str | None
    retrieval_status: str = "FOUND"
    value: str | None = None

    @property
    def normalized_source(self) -> str:
        return self.source.strip().lower()

    @property
    def normalized_status(self) -> str:
        return self.retrieval_status.strip().upper()

    @property
    def is_current_source_record(self) -> bool:
        return (
            self.normalized_source in CURRENT_SOURCE_SYSTEMS
            and self.normalized_status == "FOUND"
            and bool(self.source_record_id)
        )


@dataclass(frozen=True)
class HypothesisResult:
    hypothesis: str
    resolution_label: str
    confidence: Decimal | int | str
    evidence_for: tuple[EvidenceFact, ...] = field(default_factory=tuple)
    evidence_against: tuple[EvidenceFact, ...] = field(default_factory=tuple)
    missing_evidence: tuple[str, ...] = field(default_factory=tuple)
    materiality_amount: Decimal | int | str = Decimal("0")
    required_evidence_present: bool = True
    force_human_review: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _decimal(self.confidence))
        object.__setattr__(self, "materiality_amount", _decimal(self.materiality_amount))
        object.__setattr__(self, "evidence_for", tuple(self.evidence_for))
        object.__setattr__(self, "evidence_against", tuple(self.evidence_against))
        object.__setattr__(self, "missing_evidence", tuple(self.missing_evidence))

    @property
    def normalized_hypothesis(self) -> str:
        return self.hypothesis.strip().lower()

    @property
    def current_evidence_sources(self) -> frozenset[str]:
        return frozenset(
            fact.normalized_source
            for fact in self.evidence_for
            if fact.is_current_source_record
        )


@dataclass(frozen=True)
class RecommendationPolicy:
    high_confidence_threshold: Decimal | int | str = Decimal("0.90")
    medium_confidence_threshold: Decimal | int | str = Decimal("0.70")
    materiality_ceiling: Decimal | int | str = Decimal("500.00")
    minimum_current_sources_for_auto: int = 2
    auto_resolve_hypotheses: frozenset[str] = frozenset({"gateway_fee"})

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "high_confidence_threshold",
            _decimal(self.high_confidence_threshold),
        )
        object.__setattr__(
            self,
            "medium_confidence_threshold",
            _decimal(self.medium_confidence_threshold),
        )
        object.__setattr__(self, "materiality_ceiling", _decimal(self.materiality_ceiling))
        object.__setattr__(
            self,
            "auto_resolve_hypotheses",
            frozenset(h.strip().lower() for h in self.auto_resolve_hypotheses),
        )


@dataclass(frozen=True)
class Recommendation:
    resolution: str
    action: RecommendationAction
    reason: str
    human_intervention_required: bool
    missing_item: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "resolution": self.resolution,
            "action": self.action.value,
            "reason": self.reason,
            "human_intervention_required": self.human_intervention_required,
        }
        if self.missing_item is not None:
            payload["missing_item"] = self.missing_item
        return payload


def recommend_action(
    leading: HypothesisResult,
    policy: RecommendationPolicy | None = None,
) -> Recommendation:
    """Return the deterministic recommendation for the leading hypothesis."""

    active_policy = policy or RecommendationPolicy()
    hypothesis = leading.normalized_hypothesis

    if hypothesis == "timing_difference":
        return Recommendation(
            resolution=leading.resolution_label,
            action=RecommendationAction.WAIT_FOR_SETTLEMENT,
            reason="The leading cause is a settlement timing difference.",
            human_intervention_required=False,
        )

    if leading.missing_evidence:
        return Recommendation(
            resolution=leading.resolution_label,
            action=RecommendationAction.REQUEST_MISSING_EVIDENCE,
            reason="Required evidence is incomplete.",
            human_intervention_required=True,
            missing_item=", ".join(leading.missing_evidence),
        )

    if hypothesis in UNKNOWN_HYPOTHESES:
        return _human_review(
            leading,
            "The leading hypothesis is unknown, so safe abstention is required.",
        )

    if leading.force_human_review:
        return _human_review(leading, "Challenge checks forced human review.")

    if leading.confidence < active_policy.medium_confidence_threshold:
        return _human_review(
            leading,
            "Confidence is below the investigation threshold.",
        )

    auto_blocker = _auto_resolve_blocker(leading, active_policy)
    if auto_blocker is None:
        return Recommendation(
            resolution=leading.resolution_label,
            action=RecommendationAction.AUTO_RESOLVE,
            reason="High-confidence source-backed evidence satisfies auto-resolution policy.",
            human_intervention_required=False,
        )

    return _human_review(leading, auto_blocker)


def _auto_resolve_blocker(
    leading: HypothesisResult,
    policy: RecommendationPolicy,
) -> str | None:
    if leading.normalized_hypothesis not in policy.auto_resolve_hypotheses:
        return "The resolution type is not approved for auto-resolution."

    if leading.confidence < policy.high_confidence_threshold:
        return "Confidence is not HIGH under the configured threshold."

    if not leading.required_evidence_present:
        return "Required evidence is not fully present."

    if leading.evidence_against:
        return "Contradicting evidence is present."

    if leading.materiality_amount > policy.materiality_ceiling:
        return "The discrepancy exceeds the auto-resolution materiality ceiling."

    current_sources = leading.current_evidence_sources
    if len(current_sources) < policy.minimum_current_sources_for_auto:
        return "Fewer than two current source systems have source-backed evidence."

    return None


def _human_review(leading: HypothesisResult, reason: str) -> Recommendation:
    resolution = "unknown" if leading.normalized_hypothesis in UNKNOWN_HYPOTHESES else leading.resolution_label
    return Recommendation(
        resolution=resolution,
        action=RecommendationAction.HUMAN_REVIEW,
        reason=reason,
        human_intervention_required=True,
    )


def rank_and_recommend(
    hypotheses: Iterable[HypothesisResult],
    policy: RecommendationPolicy | None = None,
) -> Recommendation:
    """Choose the highest-confidence result deterministically, then gate it."""

    candidates = tuple(hypotheses)
    if not candidates:
        return Recommendation(
            resolution="unknown",
            action=RecommendationAction.HUMAN_REVIEW,
            reason="No hypothesis result was provided.",
            human_intervention_required=True,
        )

    leading = sorted(
        candidates,
        key=lambda result: (
            result.confidence,
            result.normalized_hypothesis not in UNKNOWN_HYPOTHESES,
            result.normalized_hypothesis,
        ),
        reverse=True,
    )[0]
    return recommend_action(leading, policy)
