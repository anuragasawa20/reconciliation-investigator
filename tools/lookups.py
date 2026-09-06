"""Plain, source-backed lookup tools.

These functions deliberately contain no model calls or inference.  They read
the three fixture CSVs and the SQLite memory database, filter by identifiers,
and return a common, auditable result shape.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any, Iterable


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _rows(filename: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _result(source: str, records: Iterable[dict[str, Any]], *, query: dict[str, Any] | None = None) -> dict[str, Any]:
    found = list(records)
    return {
        "status": "FOUND" if found else "NOT_FOUND",
        "source": source,
        "records": found,
        "count": len(found),
        "query": query or {},
    }


def get_erp_transaction(transaction_id: str | None = None, invoice_id: str | None = None) -> dict[str, Any]:
    """Return ERP records filtered by transaction ID or invoice ID."""
    rows = _rows("erp.csv")
    records = [r for r in rows if (transaction_id and r["transaction_id"] == transaction_id)
               or (invoice_id and r["invoice_id"] == invoice_id)]
    return _result("erp", records, query={"transaction_id": transaction_id, "invoice_id": invoice_id})


def get_gateway_transaction(transaction_id: str | None = None, invoice_id: str | None = None) -> dict[str, Any]:
    """Return gateway records filtered by gateway transaction or invoice ID."""
    rows = _rows("gateway.csv")
    records = [r for r in rows if (transaction_id and r["transaction_id"] == transaction_id)
               or (invoice_id and r["invoice_id"] == invoice_id)]
    return _result("gateway", records, query={"transaction_id": transaction_id, "invoice_id": invoice_id})


def get_fee_configuration(gateway_transaction_id: str | None = None, invoice_id: str | None = None) -> dict[str, Any]:
    """Return the recorded fee and rate for one gateway transaction."""
    result = get_gateway_transaction(gateway_transaction_id, invoice_id)
    records = [
        {"transaction_id": row["transaction_id"], "invoice_id": row["invoice_id"],
         "fee": row["fee"], "fee_rate": row["fee_rate"], "currency": row["currency"]}
        for row in result["records"]
    ]
    return _result("gateway", records, query={"gateway_transaction_id": gateway_transaction_id, "invoice_id": invoice_id})


def get_bank_settlement(settlement_id: str | None = None, invoice_id: str | None = None) -> dict[str, Any]:
    """Return bank settlements by settlement ID, or resolve one from an invoice."""
    if invoice_id and not settlement_id:
        gateway = get_gateway_transaction(invoice_id=invoice_id)
        settlement_ids = {row["settlement_id"] for row in gateway["records"] if row["settlement_id"]}
    else:
        settlement_ids = {settlement_id} if settlement_id else set()
    rows = _rows("bank.csv")
    records = [row for row in rows if row["settlement_id"] in settlement_ids]
    return _result("bank", records, query={"settlement_id": settlement_id, "invoice_id": invoice_id})


def get_refund_record(gateway_transaction_id: str | None = None, invoice_id: str | None = None) -> dict[str, Any]:
    """Return a refund fact when the gateway records a positive refund amount."""
    result = get_gateway_transaction(gateway_transaction_id, invoice_id)
    records = [
        {"transaction_id": row["transaction_id"], "invoice_id": row["invoice_id"],
         "refund_amount": row["refund_amount"], "currency": row["currency"]}
        for row in result["records"] if row["refund_amount"] not in ("", "0", "0.00")
    ]
    return _result("gateway", records, query={"gateway_transaction_id": gateway_transaction_id, "invoice_id": invoice_id})


def get_related_transactions(invoice_id: str) -> dict[str, Any]:
    """Return the ERP, gateway, and bank records related to an invoice."""
    erp = get_erp_transaction(invoice_id=invoice_id)["records"]
    gateway = get_gateway_transaction(invoice_id=invoice_id)["records"]
    bank = get_bank_settlement(invoice_id=invoice_id)["records"]
    records = [{"erp": erp, "gateway": gateway, "bank": bank}]
    return _result("erp+gateway+bank", records, query={"invoice_id": invoice_id}) if any((erp, gateway, bank)) else _result("erp+gateway+bank", [], query={"invoice_id": invoice_id})


def search_historical_cases(case_features: dict[str, Any] | None = None, limit: int = 5) -> dict[str, Any]:
    """Return seeded cases matching supplied exact features, newest first."""
    case_features = case_features or {}
    allowed = {"invoice_id", "currency", "pattern", "resolution"}
    filters = {key: value for key, value in case_features.items() if key in allowed and value is not None}
    clauses = [f"{key} = ?" for key in filters]
    query = "SELECT case_id, invoice_id, currency, difference_minor, pattern, resolution, confidence, case_summary, human_override, created_at FROM reconciliation_cases"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, case_id DESC LIMIT ?"
    with sqlite3.connect(DATA_DIR / "historical_cases.db") as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, [*filters.values(), max(0, int(limit))]).fetchall()
    return _result("historical_cases", [dict(row) for row in rows], query={"case_features": case_features, "limit": limit})


def get_reconciliation_case(case_id: str | None = None, invoice_id: str | None = None) -> dict[str, Any]:
    """Return finalized case-memory rows filtered by case or invoice ID."""
    query = "SELECT case_id, invoice_id, currency, difference_minor, pattern, resolution, confidence, case_summary, human_override, created_at FROM reconciliation_cases"
    values: list[Any] = []
    if case_id:
        query += " WHERE case_id = ?"
        values.append(case_id)
    elif invoice_id:
        query += " WHERE invoice_id = ?"
        values.append(invoice_id)
    else:
        return _result("historical_cases", [], query={"case_id": case_id, "invoice_id": invoice_id})
    with sqlite3.connect(DATA_DIR / "historical_cases.db") as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, values).fetchall()
    return _result("historical_cases", [dict(row) for row in rows], query={"case_id": case_id, "invoice_id": invoice_id})
