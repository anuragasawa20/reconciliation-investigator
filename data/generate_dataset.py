#!/usr/bin/env python3
"""Generate the deterministic demo dataset used by the reconciliation MVP.

The generated files intentionally contain a ``ground_truth_label`` column.  It
is test/demo metadata, not a field used by the investigator when reconciling
records.  Running this script again produces byte-for-byte identical output.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path


SEED = 20260906
DEFAULT_ROWS = 600

ERP_FIELDS = ["transaction_id", "invoice_id", "amount", "currency", "date", "status", "ground_truth_label"]
GATEWAY_FIELDS = [
    "transaction_id", "invoice_id", "amount", "currency", "date", "status",
    "fee", "fee_rate", "settlement_id", "refund_amount", "ground_truth_label",
]
BANK_FIELDS = [
    "transaction_id", "settlement_id", "amount", "settled_amount", "currency",
    "date", "settlement_date", "status", "ground_truth_label",
]

DEMO_IDS = {
    "easy_win": ("INV-DEMO-FEE-0001", "gateway_fee", "AUTO_RESOLVE"),
    "investigation": ("INV-DEMO-REFUND-0001", "refund", "HUMAN_REVIEW"),
    "hard_case": ("INV-DEMO-AMBIG-0001", "ambiguous", "ESCALATE"),
}


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_rows(count: int) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    if count < 20:
        raise ValueError("count must be at least 20 so every scenario is represented")

    erp: list[dict[str, str]] = []
    gateway: list[dict[str, str]] = []
    bank: list[dict[str, str]] = []
    cases: list[dict[str, str]] = []
    start = date(2026, 8, 1)

    # The first four IDs are stable, named demo fixtures.  The remaining rows
    # are also deterministic and provide a useful volume for the dashboard.
    scenarios: list[tuple[str, str]] = [
        ("DEMO-FEE-0001", "gateway_fee"),
        ("DEMO-REFUND-0001", "refund"),
        ("DEMO-AMBIG-0001", "ambiguous"),
        ("DEMO-UNKNOWN-0001", "unknown_other"),
    ]
    scenario_cycle = ["clean", "gateway_fee", "refund", "timing_difference", "ambiguous", "unknown_other"]
    for index in range(count):
        if index < len(scenarios):
            suffix, label = scenarios[index]
        else:
            suffix = f"{index + 1:04d}"
            relative_index = index - len(scenarios)
            # Keep the fixture representative of a real reconciliation run:
            # most rows are clean, with a smaller but useful exception set.
            label = "clean" if relative_index < 500 else scenario_cycle[1 + ((relative_index - 500) % 5)]

        invoice = f"INV-{suffix}"
        erp_id = f"ERP-{suffix}"
        gateway_id = f"PAY-{suffix}"
        settlement_id = f"SET-{suffix}"
        bank_id = f"BANK-{suffix}"
        tx_date = start + timedelta(days=index % 31)
        amount = Decimal(1000 + ((index * 137) % 19000))
        fee_rate = Decimal("0.03")
        fee = (amount * fee_rate).quantize(Decimal("0.01"))
        refund = Decimal("0.00")
        settled = amount
        settlement_date = tx_date + timedelta(days=2)
        bank_status = "SETTLED"

        if label == "gateway_fee":
            settled = amount - fee
        elif label == "refund":
            refund = (amount * Decimal("0.25")).quantize(Decimal("0.01"))
            settled = amount - refund
            fee = Decimal("0.00")
        elif label == "timing_difference":
            bank_status = "PENDING"
            settlement_date = tx_date + timedelta(days=1)
            # No bank row: the expected settlement has not arrived yet.
        elif label == "ambiguous":
            # Both explanations independently fit the shortfall.  This is a
            # deliberately non-resolvable case, not merely an unknown case.
            refund = (amount * Decimal("0.03")).quantize(Decimal("0.01"))
            settled = amount - refund
        elif label == "unknown_other":
            settled = amount - Decimal("77.77")
            fee = Decimal("0.00")

        if label == "clean":
            fee = Decimal("0.00")
            fee_rate = Decimal("0.00")

        erp.append({
            "transaction_id": erp_id, "invoice_id": invoice, "amount": money(amount),
            "currency": "INR", "date": tx_date.isoformat(), "status": "POSTED",
            "ground_truth_label": label,
        })
        gateway.append({
            "transaction_id": gateway_id, "invoice_id": invoice, "amount": money(amount),
            "currency": "INR", "date": tx_date.isoformat(), "status": "CAPTURED",
            "fee": money(fee), "fee_rate": money(fee_rate), "settlement_id": settlement_id,
            "refund_amount": money(refund), "ground_truth_label": label,
        })
        if label != "timing_difference":
            bank.append({
                "transaction_id": bank_id, "settlement_id": settlement_id, "amount": money(amount),
                "settled_amount": money(settled), "currency": "INR", "date": tx_date.isoformat(),
                "settlement_date": settlement_date.isoformat(), "status": bank_status,
                "ground_truth_label": label,
            })

        if label in {"gateway_fee", "refund", "timing_difference", "ambiguous", "unknown_other"}:
            action = "AUTO_RESOLVE" if label == "gateway_fee" else "ESCALATE"
            cases.append({
                "case_id": f"CASE-{invoice}", "invoice_id": invoice, "currency": "INR",
                "difference_minor": str(int((amount - settled) * 100)) if label != "timing_difference" else "0",
                "pattern": label, "resolution": action, "confidence": "0.96" if label == "gateway_fee" else "0.35",
                "case_summary": f"Seed case for {label} ({invoice})", "human_override": "" if label == "gateway_fee" else "REVIEW_REQUIRED",
                "created_at": "2026-09-06T00:00:00Z",
            })
    return erp, gateway, bank, cases


def write_database(path: Path, cases: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS reconciliation_cases (
            case_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, currency TEXT NOT NULL,
            difference_minor INTEGER NOT NULL, pattern TEXT NOT NULL, resolution TEXT NOT NULL,
            confidence REAL NOT NULL, case_summary TEXT NOT NULL, human_override TEXT,
            created_at TEXT NOT NULL)""")
        # A generated database is an artifact, so regeneration must not leave
        # rows from an earlier --count invocation behind.
        connection.execute("DELETE FROM reconciliation_cases")
        connection.executemany("""INSERT OR REPLACE INTO reconciliation_cases
            (case_id, invoice_id, currency, difference_minor, pattern, resolution,
             confidence, case_summary, human_override, created_at)
            VALUES (:case_id, :invoice_id, :currency, :difference_minor, :pattern,
             :resolution, :confidence, :case_summary, :human_override, :created_at)""", cases)
        connection.commit()


def generate(output_dir: Path, count: int = DEFAULT_ROWS) -> None:
    erp, gateway, bank, cases = build_rows(count)
    write_csv(output_dir / "erp.csv", ERP_FIELDS, erp)
    write_csv(output_dir / "gateway.csv", GATEWAY_FIELDS, gateway)
    write_csv(output_dir / "bank.csv", BANK_FIELDS, bank)
    write_database(output_dir / "historical_cases.db", cases)
    (output_dir / "demo_manifest.csv").write_text(
        "scenario,invoice_id,ground_truth_label,expected_action\n"
        + "\n".join(f"{name},{invoice},{label},{action}" for name, (invoice, label, action) in DEMO_IDS.items())
        + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    parser.add_argument("--count", type=int, default=DEFAULT_ROWS)
    args = parser.parse_args()
    generate(args.output_dir, args.count)
