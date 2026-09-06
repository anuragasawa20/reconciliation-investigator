"""Historical-case retrieval and write-side audit persistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DB_PATH = Path(__file__).resolve().parents[1] / "state.db"


def _ensure_status_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS case_status (
            case_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
            status TEXT NOT NULL, proposed_resolution TEXT,
            human_override TEXT, reason TEXT, updated_at TEXT NOT NULL
        )"""
    )


def _ensure_memory_table(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS reconciliation_cases (
        case_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, currency TEXT NOT NULL,
        difference_minor INTEGER NOT NULL, pattern TEXT NOT NULL, resolution TEXT NOT NULL,
        confidence REAL NOT NULL, case_summary TEXT NOT NULL, human_override TEXT,
        created_at TEXT NOT NULL)""")


def apply_resolution(
    *, invoice_id: str, case_id: str | None = None, resolution: str,
    status: str, proposed_resolution: str | None = None,
    human_override: str | None = None, reason: str = "",
    db_path: str | Path = DB_PATH,
) -> dict[str, Any]:
    """Persist the finalized decision and its audit status without side effects."""
    case_id = case_id or f"CASE-{invoice_id}"
    timestamp = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        _ensure_status_table(connection)
        connection.execute(
            """INSERT OR REPLACE INTO case_status
            (case_id, invoice_id, status, proposed_resolution, human_override, reason, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (case_id, invoice_id, status, proposed_resolution or resolution,
             human_override, reason, timestamp),
        )
        connection.commit()
    return {"case_id": case_id, "invoice_id": invoice_id, "status": status, "updated_at": timestamp}


def store_case_memory(
    *, invoice_id: str, pattern: str, resolution: str, confidence: float,
    difference_minor: int = 0, currency: str = "INR", case_summary: str = "",
    human_override: str | None = None, case_id: str | None = None,
    db_path: str | Path = DB_PATH,
) -> dict[str, Any]:
    """Store one finalized case while preserving proposed and overridden outcomes."""
    case_id = case_id or f"CASE-{invoice_id}"
    timestamp = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        _ensure_memory_table(connection)
        connection.execute(
            """INSERT OR REPLACE INTO reconciliation_cases
            (case_id, invoice_id, currency, difference_minor, pattern, resolution,
             confidence, case_summary, human_override, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, invoice_id, currency, difference_minor, pattern, resolution,
             confidence, case_summary, human_override, timestamp),
        )
        connection.commit()
    return {"case_id": case_id, "invoice_id": invoice_id, "resolution": resolution}


def search_historical_cases(
    case_features: Mapping[str, Any] | None = None,
    limit: int = 5,
    db_path: str | Path = DB_PATH,
) -> dict[str, Any]:
    """Return finalized runtime cases matching exact supplied features."""
    case_features = case_features or {}
    allowed = {"invoice_id", "currency", "pattern", "resolution"}
    filters = {key: value for key, value in case_features.items() if key in allowed and value is not None}
    clauses = [f"{key} = ?" for key in filters]
    query = "SELECT case_id, invoice_id, currency, difference_minor, pattern, resolution, confidence, case_summary, human_override, created_at FROM reconciliation_cases"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, case_id DESC LIMIT ?"
    with sqlite3.connect(db_path) as connection:
        _ensure_memory_table(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, [*filters.values(), max(0, int(limit))]).fetchall()
    records = [dict(row) for row in rows]
    return {
        "status": "FOUND" if records else "NOT_FOUND",
        "source": "runtime_memory",
        "records": records,
        "count": len(records),
        "query": {"case_features": case_features, "limit": limit},
    }


def get_reconciliation_case(
    case_id: str | None = None,
    invoice_id: str | None = None,
    db_path: str | Path = DB_PATH,
) -> dict[str, Any]:
    """Return finalized runtime case-memory rows filtered by case or invoice."""
    values: list[Any] = []
    query = "SELECT case_id, invoice_id, currency, difference_minor, pattern, resolution, confidence, case_summary, human_override, created_at FROM reconciliation_cases"
    if case_id:
        query += " WHERE case_id = ?"
        values.append(case_id)
    elif invoice_id:
        query += " WHERE invoice_id = ?"
        values.append(invoice_id)
    else:
        return {
            "status": "NOT_FOUND",
            "source": "runtime_memory",
            "records": [],
            "count": 0,
            "query": {"case_id": case_id, "invoice_id": invoice_id},
        }
    with sqlite3.connect(db_path) as connection:
        _ensure_memory_table(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, values).fetchall()
    records = [dict(row) for row in rows]
    return {
        "status": "FOUND" if records else "NOT_FOUND",
        "source": "runtime_memory",
        "records": records,
        "count": len(records),
        "query": {"case_id": case_id, "invoice_id": invoice_id},
    }


__all__ = ["search_historical_cases", "get_reconciliation_case", "apply_resolution", "store_case_memory"]
