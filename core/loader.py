from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ERP_REQUIRED = ("transaction_id", "invoice_id", "amount", "currency", "date", "status")
GATEWAY_REQUIRED = (
    "transaction_id",
    "invoice_id",
    "amount",
    "currency",
    "date",
    "status",
)
BANK_REQUIRED = (
    "transaction_id",
    "amount",
    "settled_amount",
    "currency",
    "date",
    "settlement_date",
    "status",
)


@dataclass(frozen=True)
class RowError:
    source: str
    row_number: int
    message: str
    row: dict[str, str]


@dataclass(frozen=True)
class LoadedRows:
    source: str
    rows: list[dict[str, str]]
    errors: list[RowError]


@dataclass(frozen=True)
class LoadedDatasets:
    erp: LoadedRows
    gateway: LoadedRows
    bank: LoadedRows

    @property
    def errors(self) -> list[RowError]:
        return [*self.erp.errors, *self.gateway.errors, *self.bank.errors]


def load_csv(path: str | Path, source: str, required_columns: Iterable[str]) -> LoadedRows:
    path = Path(path)
    required = tuple(required_columns)
    errors: list[RowError] = []
    accepted: list[dict[str, str]] = []

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        missing = [column for column in required if column not in fieldnames]
        if missing:
            return LoadedRows(
                source=source,
                rows=[],
                errors=[
                    RowError(
                        source=source,
                        row_number=0,
                        message=f"Missing required columns: {', '.join(missing)}",
                        row={},
                    )
                ],
            )

        for row_number, row in enumerate(reader, start=2):
            clean_row = {key: (value or "").strip() for key, value in row.items()}
            blank_required = [column for column in required if not clean_row.get(column)]
            if blank_required:
                errors.append(
                    RowError(
                        source=source,
                        row_number=row_number,
                        message=f"Blank required values: {', '.join(blank_required)}",
                        row=clean_row,
                    )
                )
                continue
            accepted.append(clean_row)

    return LoadedRows(source=source, rows=accepted, errors=errors)


def load_datasets(data_dir: str | Path = "data") -> LoadedDatasets:
    data_path = Path(data_dir)
    return LoadedDatasets(
        erp=load_csv(data_path / "erp_transactions.csv", "erp", ERP_REQUIRED),
        gateway=load_csv(data_path / "gateway_transactions.csv", "gateway", GATEWAY_REQUIRED),
        bank=load_csv(data_path / "bank_transactions.csv", "bank", BANK_REQUIRED),
    )

