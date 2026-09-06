from __future__ import annotations

from dataclasses import dataclass

from core.matcher import ReconciliationRecord, ReconciliationResult


@dataclass(frozen=True)
class ExceptionSplit:
    matched: list[ReconciliationRecord]
    exceptions: list[ReconciliationRecord]


def split_exceptions(result: ReconciliationResult) -> ExceptionSplit:
    return ExceptionSplit(matched=result.matched, exceptions=result.exceptions)

