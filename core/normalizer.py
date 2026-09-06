from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from core.loader import LoadedDatasets, RowError


SourceName = Literal["erp", "gateway", "bank"]
NORMALIZATION_VERSION = "1.0.0"


@dataclass(frozen=True)
class NormalizedRecord:
    source: SourceName
    raw: dict[str, str]
    transaction_id: str
    invoice_id: str | None
    settlement_id: str | None
    amount: Decimal
    settled_amount: Decimal | None
    currency: str
    transaction_date: date
    settlement_date: date | None
    status: str
    fee: Decimal | None = None
    fee_rate: Decimal | None = None
    refund_amount: Decimal | None = None
    normalization_version: str = NORMALIZATION_VERSION


@dataclass(frozen=True)
class NormalizedDatasets:
    erp: list[NormalizedRecord]
    gateway: list[NormalizedRecord]
    bank: list[NormalizedRecord]
    errors: list[RowError]


def normalize_invoice_id(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"[\s_-]+", "", value.strip().upper())
    match = re.fullmatch(r"INV0*(\d+)", compact)
    if match:
        return f"INV-{int(match.group(1)):04d}"
    return value.strip().upper()


def normalize_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def parse_decimal(value: str | None, *, default: Decimal | None = None) -> Decimal | None:
    if value is None or value.strip() == "":
        return default
    try:
        return Decimal(value.strip()).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc


def parse_rate(value: str | None) -> Decimal | None:
    if value is None or value.strip() == "":
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid ISO date: {value}") from exc


def normalize_currency(value: str) -> str:
    currency = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError(f"Invalid currency code: {value}")
    return currency


def _normalize_row(source: SourceName, row: dict[str, str]) -> NormalizedRecord:
    transaction_date = parse_date(row["date"])
    if source == "erp":
        return NormalizedRecord(
            source=source,
            raw=row,
            transaction_id=normalize_identifier(row["transaction_id"]) or "",
            invoice_id=normalize_invoice_id(row["invoice_id"]),
            settlement_id=None,
            amount=parse_decimal(row["amount"]) or Decimal("0.00"),
            settled_amount=None,
            currency=normalize_currency(row["currency"]),
            transaction_date=transaction_date,
            settlement_date=None,
            status=row["status"].strip().upper(),
        )
    if source == "gateway":
        return NormalizedRecord(
            source=source,
            raw=row,
            transaction_id=normalize_identifier(row["transaction_id"]) or "",
            invoice_id=normalize_invoice_id(row["invoice_id"]),
            settlement_id=normalize_identifier(row.get("settlement_id")),
            amount=parse_decimal(row["amount"]) or Decimal("0.00"),
            settled_amount=None,
            currency=normalize_currency(row["currency"]),
            transaction_date=transaction_date,
            settlement_date=None,
            status=row["status"].strip().upper(),
            fee=parse_decimal(row.get("fee"), default=Decimal("0.00")),
            fee_rate=parse_rate(row.get("fee_rate")),
            refund_amount=parse_decimal(row.get("refund_amount"), default=Decimal("0.00")),
        )
    return NormalizedRecord(
        source=source,
        raw=row,
        transaction_id=normalize_identifier(row["transaction_id"]) or "",
        invoice_id=None,
        settlement_id=normalize_identifier(row.get("settlement_id")),
        amount=parse_decimal(row["amount"]) or Decimal("0.00"),
        settled_amount=parse_decimal(row["settled_amount"]) or Decimal("0.00"),
        currency=normalize_currency(row["currency"]),
        transaction_date=transaction_date,
        settlement_date=parse_date(row["settlement_date"]),
        status=row["status"].strip().upper(),
    )


def normalize_datasets(loaded: LoadedDatasets) -> NormalizedDatasets:
    errors = list(loaded.errors)

    def normalize_many(source: SourceName, rows: list[dict[str, str]]) -> list[NormalizedRecord]:
        records: list[NormalizedRecord] = []
        for index, row in enumerate(rows, start=2):
            try:
                records.append(_normalize_row(source, row))
            except ValueError as exc:
                errors.append(RowError(source=source, row_number=index, message=str(exc), row=row))
        return records

    return NormalizedDatasets(
        erp=normalize_many("erp", loaded.erp.rows),
        gateway=normalize_many("gateway", loaded.gateway.rows),
        bank=normalize_many("bank", loaded.bank.rows),
        errors=errors,
    )

