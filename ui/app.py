"""Streamlit client for the checkpointed reconciliation graph."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import streamlit as st
from langgraph.types import Command

from graph.reconciliation_graph import app
from tools.lookups import get_bank_settlement, get_gateway_transaction
from tools.memory_tools import DB_PATH, get_reconciliation_case

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _cases() -> list[dict[str, Any]]:
    cases = []
    for row in get_gateway_transaction()["records"]:
        invoice = row["invoice_id"]
        bank = get_bank_settlement(invoice_id=invoice)["records"]
        amount = float(row["amount"])
        settled = float(bank[0]["settled_amount"]) if bank else amount
        stored = get_reconciliation_case(invoice_id=invoice)["records"]
        case = {"invoice_id": invoice, "amount": amount, "settled": settled,
                "difference": amount - settled, "stored": stored[0] if stored else None}
        if case["difference"] > 0:
            cases.append(case)
    return cases


def _status(invoice_id: str) -> str:
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute("SELECT status FROM case_status WHERE invoice_id = ?", (invoice_id,)).fetchone()
    return row[0] if row else "OPEN"


def _run(invoice_id: str, value: Any) -> dict[str, Any]:
    return app.invoke(value, config={"configurable": {"thread_id": invoice_id}})


def _start(invoice_id: str) -> None:
    _run(invoice_id, {"invoice_id": invoice_id})
    st.session_state.selected_invoice = invoice_id
    st.rerun()


def _resume(invoice_id: str, decision: str) -> None:
    _run(invoice_id, Command(resume={"decision": decision}))
    st.rerun()


def investigation(case: dict[str, Any]) -> None:
    invoice = case["invoice_id"]
    if st.button("← Back to overview"):
        st.session_state.selected_invoice = None
        st.rerun()
    st.title(f"Investigation · {invoice}")
    st.caption(f"Live graph thread: {invoice} · status: {_status(invoice)}")
    cols = st.columns(3)
    cols[0].metric("ERP / gateway", f"₹{case['amount']:,.2f}")
    cols[1].metric("Bank settled", f"₹{case['settled']:,.2f}")
    cols[2].metric("Difference", f"₹{case['difference']:,.2f}")
    stored = case.get("stored")
    if stored:
        st.info(f"Memory: {stored['pattern']} → {stored['resolution']} (confidence {stored['confidence']:.0%})")
    try:
        snapshot = app.get_state({"configurable": {"thread_id": invoice}})
        values = snapshot.values or {}
        recommendation = values.get("recommendation", {})
        if recommendation:
            st.subheader("Graph recommendation")
            st.write(recommendation.get("reason", ""))
            st.json(recommendation)
        if snapshot.next:
            st.subheader("Human review")
            st.warning("The graph is paused. Choose a decision to resume this invoice thread.")
            approve, reject = st.columns(2)
            with approve:
                if st.button("Approve", type="primary", use_container_width=True):
                    _resume(invoice, "approve")
            with reject:
                if st.button("Reject", use_container_width=True):
                    _resume(invoice, "reject")
        else:
            st.success(f"Graph complete: {_status(invoice)}")
    except Exception as exc:
        st.error(f"Graph error: {exc}")


def main() -> None:
    st.set_page_config(page_title="Reconciliation Investigator", page_icon="◎", layout="wide")
    st.title("Reconciliation control room")
    st.caption("Every investigation runs on the checkpointed graph; invoice ID is the thread ID.")
    st.session_state.setdefault("selected_invoice", None)
    cases = _cases()
    if st.session_state.selected_invoice:
        case = next(item for item in cases if item["invoice_id"] == st.session_state.selected_invoice)
        investigation(case)
        return
    st.subheader("Exception queue")
    for case in cases[:30]:
        cols = st.columns([2.4, 1.4, 1.4, 1.2])
        cols[0].write(case["invoice_id"])
        cols[1].write(f"₹{case['difference']:,.2f}")
        cols[2].write(_status(case["invoice_id"]))
        if cols[3].button("Investigate", key=f"investigate-{case['invoice_id']}"):
            _start(case["invoice_id"])


if __name__ == "__main__":
    main()
