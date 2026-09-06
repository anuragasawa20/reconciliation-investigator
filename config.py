"""Shared configuration for reconciliation investigation.

Environment variables intentionally control only integration behavior. Finance
thresholds and controlled hypothesis names stay explicit in code so tests and
audits can pin the actual decision boundary.
"""

from __future__ import annotations

import os
from decimal import Decimal


ROUNDING_TOLERANCE = Decimal("0.01")
MIN_EVIDENCE_FOR_AUTO = 2
AUTO_RESOLVE_CEILING = Decimal("500.00")
HIGH_CONFIDENCE_THRESHOLD = Decimal("0.90")
MEDIUM_CONFIDENCE_THRESHOLD = Decimal("0.70")
CHALLENGE_RETRY_CAP = 1

LLM_PROVIDER = os.getenv("RECONCILIATION_LLM_PROVIDER", "openrouter").strip().lower()
LLM_MODEL = os.getenv(
    "RECONCILIATION_LLM_MODEL",
    os.getenv("OPENROUTER_MODEL", os.getenv("OPENAI_MODEL", "openrouter/free")),
)
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_APP_REFERER = os.getenv("OPENROUTER_APP_REFERER", "")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "Reconciliation Investigator")
LLM_TIMEOUT_SECONDS = float(os.getenv("RECONCILIATION_LLM_TIMEOUT_SECONDS", "45"))
LLM_ENABLED = os.getenv("RECONCILIATION_LLM_ENABLED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
