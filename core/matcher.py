from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from core.normalizer import NormalizedDatasets, NormalizedRecord


MatchStatus = Literal["MATCHED", "PARTIAL_MATCH", "MISMATCH", "UNMATCHED", "AMBIGUOUS"]
PairName = Literal["erp_gateway", "erp_bank", "gateway_bank"]


@dataclass(frozen=True)
class MatchConfig:
    amount_tolerance: Decimal = Decimal("0.01")
    settlement_window_days: int = 2


@dataclass(frozen=True)
class PairwiseMatch:
    pair: PairName
    status: MatchStatus
    difference: Decimal | None
    matched_by: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReconciliationRecord:
    invoice_id: str
    erp: list[NormalizedRecord]
    gateway: list[NormalizedRecord]
    bank: list[NormalizedRecord]
    pairwise: list[PairwiseMatch]
    match_status: MatchStatus
    matched_by: list[str]
    warnings: list[str]
    exception_reasons: list[str]

    @property
    def is_exception(self) -> bool:
        return bool(self.exception_reasons)

    @property
    def canonical_transaction(self) -> dict[str, str | None]:
        erp = self.erp[0] if len(self.erp) == 1 else None
        gateway = self.gateway[0] if len(self.gateway) == 1 else None
        bank = self.bank[0] if len(self.bank) == 1 else None
        currency = next(
            (record.currency for record in (erp, gateway, bank) if record is not None),
            None,
        )
        return {
            "invoice_id": self.invoice_id,
            "erp_transaction_id": erp.transaction_id if erp else None,
            "gateway_transaction_id": gateway.transaction_id if gateway else None,
            "bank_transaction_id": bank.transaction_id if bank else None,
            "settlement_id": gateway.settlement_id if gateway else bank.settlement_id if bank else None,
            "currency": currency,
            "expected_amount": str(erp.amount) if erp else None,
            "gateway_amount": str(gateway.amount) if gateway else None,
            "gateway_fee": str(gateway.fee) if gateway and gateway.fee is not None else None,
            "bank_amount": str(bank.settled_amount or bank.amount) if bank else None,
            "transaction_date": str(erp.transaction_date if erp else gateway.transaction_date if gateway else bank.transaction_date if bank else ""),
            "settlement_date": str(bank.settlement_date) if bank and bank.settlement_date else None,
        }


@dataclass(frozen=True)
class ReconciliationResult:
    records: list[ReconciliationRecord]

    @property
    def matched(self) -> list[ReconciliationRecord]:
        return [record for record in self.records if not record.is_exception]

    @property
    def exceptions(self) -> list[ReconciliationRecord]:
        return [record for record in self.records if record.is_exception]


def _is_exact(left: Decimal, right: Decimal) -> bool:
    return left == right


def _within_tolerance(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    return abs(left - right) <= tolerance


def _amount_match_status(left: Decimal, right: Decimal, config: MatchConfig) -> tuple[MatchStatus, Decimal, list[str]]:
    difference = (left - right).copy_abs().quantize(Decimal("0.01"))
    if _is_exact(left, right):
        return "MATCHED", difference, ["amount_exact"]
    if _within_tolerance(left, right, config.amount_tolerance):
        return "MATCHED", difference, ["amount_rounding_tolerance"]
    return "MISMATCH", difference, []


def _date_outside_window(left: date | None, right: date | None, window_days: int) -> bool:
    if left is None or right is None:
        return False
    return abs((right - left).days) > window_days


def _bank_records_for_gateway(gateway: list[NormalizedRecord], banks_by_settlement: dict[str, list[NormalizedRecord]]) -> list[NormalizedRecord]:
    banks: list[NormalizedRecord] = []
    seen: set[str] = set()
    for payment in gateway:
        if not payment.settlement_id:
            continue
        for bank in banks_by_settlement.get(payment.settlement_id, []):
            if bank.transaction_id not in seen:
                banks.append(bank)
                seen.add(bank.transaction_id)
    return banks


def _pair_erp_gateway(erp: list[NormalizedRecord], gateway: list[NormalizedRecord], config: MatchConfig) -> PairwiseMatch:
    if not erp or not gateway:
        return PairwiseMatch("erp_gateway", "UNMATCHED", None, [], ["missing_source_record"])
    if len(erp) > 1 or len(gateway) > 1:
        return PairwiseMatch("erp_gateway", "AMBIGUOUS", None, ["invoice_id"], ["multiple_candidate_records"])
    if erp[0].currency != gateway[0].currency:
        return PairwiseMatch("erp_gateway", "MISMATCH", None, ["invoice_id"], ["currency_mismatch"])
    status, difference, amount_rule = _amount_match_status(erp[0].amount, gateway[0].amount, config)
    return PairwiseMatch("erp_gateway", status, difference, ["invoice_id", *amount_rule])


def _pair_gateway_bank(gateway: list[NormalizedRecord], bank: list[NormalizedRecord], config: MatchConfig) -> PairwiseMatch:
    if not gateway or not bank:
        return PairwiseMatch("gateway_bank", "UNMATCHED", None, [], ["missing_source_record"])
    if len(gateway) > 1 or len(bank) > 1:
        return PairwiseMatch("gateway_bank", "AMBIGUOUS", None, ["settlement_id"], ["multiple_candidate_records"])
    payment = gateway[0]
    settlement = bank[0]
    matched_by = ["settlement_id"] if payment.settlement_id and payment.settlement_id == settlement.settlement_id else []
    warnings: list[str] = []
    if payment.currency != settlement.currency:
        return PairwiseMatch("gateway_bank", "MISMATCH", None, matched_by, ["currency_mismatch"])
    expected_settlement = payment.amount - (payment.fee or Decimal("0.00")) - (payment.refund_amount or Decimal("0.00"))
    status, difference, amount_rule = _amount_match_status(expected_settlement, settlement.settled_amount or settlement.amount, config)
    if payment.amount != (settlement.settled_amount or settlement.amount):
        status = "PARTIAL_MATCH" if status == "MATCHED" else status
    if _date_outside_window(payment.transaction_date, settlement.settlement_date, config.settlement_window_days):
        warnings.append("settlement_outside_window")
    return PairwiseMatch("gateway_bank", status, difference, [*matched_by, *amount_rule], warnings)


def _pair_erp_bank(erp: list[NormalizedRecord], bank: list[NormalizedRecord], config: MatchConfig) -> PairwiseMatch:
    if not erp or not bank:
        return PairwiseMatch("erp_bank", "UNMATCHED", None, [], ["missing_source_record"])
    if len(erp) > 1 or len(bank) > 1:
        return PairwiseMatch("erp_bank", "AMBIGUOUS", None, [], ["multiple_candidate_records"])
    if erp[0].currency != bank[0].currency:
        return PairwiseMatch("erp_bank", "MISMATCH", None, [], ["currency_mismatch"])
    status, difference, amount_rule = _amount_match_status(erp[0].amount, bank[0].settled_amount or bank[0].amount, config)
    return PairwiseMatch("erp_bank", status, difference, amount_rule)


def reconcile(normalized: NormalizedDatasets, config: MatchConfig | None = None) -> ReconciliationResult:
    config = config or MatchConfig()
    gateways_by_invoice: dict[str, list[NormalizedRecord]] = {}
    erp_by_invoice: dict[str, list[NormalizedRecord]] = {}
    banks_by_settlement: dict[str, list[NormalizedRecord]] = {}

    for erp in normalized.erp:
        if erp.invoice_id:
            erp_by_invoice.setdefault(erp.invoice_id, []).append(erp)
    for gateway in normalized.gateway:
        if gateway.invoice_id:
            gateways_by_invoice.setdefault(gateway.invoice_id, []).append(gateway)
        if gateway.settlement_id:
            banks_by_settlement.setdefault(gateway.settlement_id, [])
    for bank in normalized.bank:
        if bank.settlement_id:
            banks_by_settlement.setdefault(bank.settlement_id, []).append(bank)

    invoice_ids = sorted(set(erp_by_invoice) | set(gateways_by_invoice))
    records: list[ReconciliationRecord] = []

    for invoice_id in invoice_ids:
        erp = erp_by_invoice.get(invoice_id, [])
        gateway = gateways_by_invoice.get(invoice_id, [])
        bank = _bank_records_for_gateway(gateway, banks_by_settlement)
        pairwise = [
            _pair_erp_gateway(erp, gateway, config),
            _pair_erp_bank(erp, bank, config),
            _pair_gateway_bank(gateway, bank, config),
        ]
        warnings = sorted({warning for match in pairwise for warning in match.warnings})
        matched_by = sorted({rule for match in pairwise for rule in match.matched_by})
        exception_reasons = _exception_reasons(erp, gateway, bank, pairwise, warnings)
        status = _overall_status(pairwise, exception_reasons)
        records.append(
            ReconciliationRecord(
                invoice_id=invoice_id,
                erp=erp,
                gateway=gateway,
                bank=bank,
                pairwise=pairwise,
                match_status=status,
                matched_by=matched_by,
                warnings=warnings,
                exception_reasons=exception_reasons,
            )
        )

    return ReconciliationResult(records=records)


def _overall_status(pairwise: list[PairwiseMatch], exception_reasons: list[str]) -> MatchStatus:
    statuses = {match.status for match in pairwise}
    if "AMBIGUOUS" in statuses:
        return "AMBIGUOUS"
    if not exception_reasons:
        return "MATCHED"
    if "UNMATCHED" in statuses:
        return "UNMATCHED"
    if "MISMATCH" in statuses:
        return "MISMATCH"
    return "PARTIAL_MATCH"


def _exception_reasons(
    erp: list[NormalizedRecord],
    gateway: list[NormalizedRecord],
    bank: list[NormalizedRecord],
    pairwise: list[PairwiseMatch],
    warnings: list[str],
) -> list[str]:
    reasons: set[str] = set()
    if len(erp) != 1 or len(gateway) != 1 or len(bank) != 1:
        reasons.add("missing_or_duplicate_source")
    for match in pairwise:
        if match.status in {"MISMATCH", "UNMATCHED", "AMBIGUOUS", "PARTIAL_MATCH"}:
            reasons.add(match.status.lower())
        for warning in match.warnings:
            reasons.add(warning)
    if len({record.currency for record in [*erp, *gateway, *bank]}) > 1:
        reasons.add("currency_mismatch")
    if any(record.status in {"FAILED", "VOID", "CANCELLED"} for record in [*erp, *gateway, *bank]):
        reasons.add("status_inconsistent")
    if "settlement_outside_window" in warnings:
        reasons.add("settlement_outside_window")
    return sorted(reasons)
