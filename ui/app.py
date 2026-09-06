"""Streamlit UI shell for the reconciliation investigator.

The UI intentionally uses local mock cases until the reconciliation graph is
available.  Keeping the view model here small makes the eventual graph
adapter a drop-in replacement for ``MOCK_CASES``.
"""

from __future__ import annotations

from typing import Any

import streamlit as st


MOCK_CASES: list[dict[str, Any]] = [
    {
        "id": "EXC-1042",
        "invoice": "INV-2026-1842",
        "merchant": "Northstar Labs",
        "status": "Needs review",
        "root_cause": "Gateway processing fee",
        "erp": 10000.00,
        "gateway": 10000.00,
        "bank": 9700.00,
        "fee": 300.00,
        "fee_rate": "3%",
        "confidence": "High",
        "confidence_score": 96,
        "narrative": (
            "ERP and gateway both record a gross amount of ₹10,000. The bank "
            "received ₹9,700, and the gateway records a 3% processing fee. "
            "That fee is ₹300, which explains the full difference."
        ),
        "supporting": [
            "Gateway fee = ₹300",
            "Configured fee rate = 3%",
            "Bank settlement = ₹9,700",
            "14 resolved cases show the same pattern",
        ],
        "checks": [
            "No refund was found",
            "No duplicate was found",
            "Settlement occurred within the normal window",
        ],
        "alternatives": "Refund and duplicate settlement checks were considered and ruled out.",
    },
    {
        "id": "EXC-1038",
        "invoice": "INV-2026-1809",
        "merchant": "Acme Retail",
        "status": "Needs review",
        "root_cause": "Refund likely explains the shortfall",
        "erp": 4250.00,
        "gateway": 4250.00,
        "bank": 3250.00,
        "fee": 0.00,
        "fee_rate": "—",
        "confidence": "Medium",
        "confidence_score": 78,
        "narrative": (
            "ERP and gateway agree on ₹4,250, while the bank settled ₹3,250. "
            "A ₹1,000 gateway refund is present for this invoice, matching the "
            "difference exactly. Human review is recommended before closing."
        ),
        "supporting": [
            "Gateway refund = ₹1,000",
            "Bank settlement = ₹3,250",
            "Refund reference matches the invoice",
        ],
        "checks": [
            "Refund record found",
            "No duplicate was found",
            "Settlement occurred within the normal window",
        ],
        "alternatives": "A gateway fee was checked, but no configured fee was present.",
    },
    {
        "id": "EXC-1029",
        "invoice": "INV-2026-1766",
        "merchant": "Orbit Foods",
        "status": "Auto-resolved",
        "root_cause": "Expected gateway processing fee",
        "erp": 1800.00,
        "gateway": 1800.00,
        "bank": 1746.00,
        "fee": 54.00,
        "fee_rate": "3%",
        "confidence": "High",
        "confidence_score": 98,
        "narrative": "The 3% gateway fee exactly explains the ₹54 difference between the gross and settled amounts.",
        "supporting": ["Gateway fee = ₹54", "Configured fee rate = 3%", "Bank settlement = ₹1,746"],
        "checks": ["No refund was found", "No duplicate was found", "Settlement window is normal"],
        "alternatives": "No competing hypothesis had supporting evidence.",
    },
    {
        "id": "EXC-1017",
        "invoice": "INV-2026-1698",
        "merchant": "Pine & Co.",
        "status": "Challenged",
        "root_cause": "Settlement discrepancy requires investigation",
        "erp": 7500.00,
        "gateway": 7500.00,
        "bank": 7500.00,
        "fee": 0.00,
        "fee_rate": "—",
        "confidence": "Low",
        "confidence_score": 41,
        "narrative": "The source amounts now agree, but the prior exception cannot be explained from the available evidence. Keep this case open for a source audit.",
        "supporting": ["ERP amount = ₹7,500", "Gateway amount = ₹7,500"],
        "checks": ["Bank record is present", "No refund was found", "No matching hypothesis cleared the evidence threshold"],
        "alternatives": "Fee, refund, and duplicate hypotheses remain inconclusive.",
    },
]


def money(value: float) -> str:
    """Format mock amounts consistently for the investigation view."""

    return f"₹{value:,.2f}"


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600;700&display=swap');
        :root { --ink:#17221e; --muted:#687570; --line:#dfe7e2; --green:#1b6b4d; --mint:#e8f4ed; --cream:#f8faf7; }
        .stApp { background:var(--cream); color:var(--ink); font-family:'DM Sans', sans-serif; }
        .block-container { max-width:1180px; padding:2.8rem 3.5rem 4rem; }
        h1,h2,h3 { color:var(--ink); letter-spacing:-.03em; }
        h1 { font-size:2.35rem; margin-bottom:.25rem; }
        h2 { font-size:1.45rem; margin-top:1.9rem; }
        .eyebrow { color:var(--green); font:500 .72rem 'DM Mono',monospace; letter-spacing:.1em; text-transform:uppercase; }
        .subtle { color:var(--muted); font-size:.95rem; }
        .metric { background:white; border:1px solid var(--line); border-radius:12px; padding:1.1rem 1.25rem; min-height:105px; }
        .metric-label { color:var(--muted); font-size:.8rem; }
        .metric-value { font-size:2rem; font-weight:700; margin-top:.3rem; }
        .metric-value.green { color:var(--green); }
        .case-card, .panel { background:white; border:1px solid var(--line); border-radius:12px; padding:1.35rem 1.5rem; }
        .case-card { margin-bottom:.8rem; }
        .case-id { color:var(--green); font:500 .82rem 'DM Mono',monospace; }
        .case-title { font-size:1.08rem; font-weight:600; margin:.22rem 0 .3rem; }
        .pill { display:inline-block; border-radius:20px; padding:.24rem .6rem; font-size:.72rem; font-weight:600; background:#eef2ef; color:#52615a; }
        .pill.green { color:#176344; background:#e5f3ea; }
        .pill.amber { color:#875b17; background:#fff2d8; }
        .pill.red { color:#91463e; background:#fbe9e7; }
        .source-amount { border:1px solid var(--line); border-radius:10px; padding:.85rem 1rem; }
        .source-name { color:var(--muted); font-size:.76rem; text-transform:uppercase; letter-spacing:.06em; }
        .source-value { font:500 1.35rem 'DM Mono',monospace; margin-top:.3rem; }
        .difference { background:var(--mint); border:1px solid #cbe4d5; border-radius:10px; padding:.85rem 1rem; }
        .narrative { background:#f3f8f4; border-left:4px solid var(--green); border-radius:0 10px 10px 0; padding:1rem 1.15rem; line-height:1.65; }
        .check { padding:.52rem 0; border-bottom:1px solid #edf1ee; font-size:.9rem; }
        .check:last-child { border-bottom:0; }
        .check-mark { color:var(--green); font-weight:700; margin-right:.5rem; }
        [data-testid='stSidebar'] { background:#f1f6f2; border-right:1px solid var(--line); }
        </style>
        """,
        unsafe_allow_html=True,
    )


def status_class(status: str) -> str:
    return {"Auto-resolved": "green", "Challenged": "red", "Needs review": "amber"}.get(status, "")


def metric(label: str, value: str, accent: bool = False) -> None:
    accent_class = " green" if accent else ""
    st.markdown(f'<div class="metric"><div class="metric-label">{label}</div><div class="metric-value{accent_class}">{value}</div></div>', unsafe_allow_html=True)


def dashboard(cases: list[dict[str, Any]]) -> None:
    st.markdown('<div class="eyebrow">Reconciliation control room</div>', unsafe_allow_html=True)
    st.title("Exception overview")
    st.markdown('<div class="subtle">A clear queue for the transactions that need an explanation.</div>', unsafe_allow_html=True)
    st.write("")
    counts = {
        "Total exceptions": len(cases),
        "Needs review": sum(c["status"] == "Needs review" for c in cases),
        "Auto-resolved": sum(c["status"] == "Auto-resolved" for c in cases),
        "Challenged": sum(c["status"] == "Challenged" for c in cases),
    }
    cols = st.columns(4)
    for col, (label, value) in zip(cols, counts.items()):
        with col:
            metric(label, str(value), label == "Auto-resolved")

    st.subheader("Open exception queue")
    for case in cases:
        cols = st.columns([1.5, 3.2, 1.5, 1.5, 1.1])
        with cols[0]:
            st.markdown(f'<div class="case-id">{case["id"]}</div><div class="subtle">{case["invoice"]}</div>', unsafe_allow_html=True)
        with cols[1]:
            st.markdown(f'<div class="case-title">{case["root_cause"]}</div><div class="subtle">{case["merchant"]} · Difference {money(case["erp"] - case["bank"])}</div>', unsafe_allow_html=True)
        with cols[2]:
            st.markdown(f'<span class="pill {status_class(case["status"])}">{case["status"]}</span>', unsafe_allow_html=True)
        with cols[3]:
            st.markdown(f'<div class="subtle">Confidence</div><strong>{case["confidence"]}</strong>', unsafe_allow_html=True)
        with cols[4]:
            if st.button("Investigate", key=f"open-{case['id']}", use_container_width=True):
                st.session_state.selected_case = case["id"]
                st.rerun()
        st.markdown('<div style="height:.1rem"></div>', unsafe_allow_html=True)


def source_amount(label: str, value: float) -> None:
    st.markdown(f'<div class="source-amount"><div class="source-name">{label}</div><div class="source-value">{money(value)}</div></div>', unsafe_allow_html=True)


def investigation(case: dict[str, Any]) -> None:
    if st.button("← Back to overview"):
        st.session_state.selected_case = None
        st.rerun()
    st.markdown(f'<div class="eyebrow">Investigation · {case["id"]}</div>', unsafe_allow_html=True)
    st.title(case["root_cause"])
    st.markdown(f'<div class="subtle">{case["merchant"]} · {case["invoice"]} · <span class="pill {status_class(case["status"])}">{case["status"]}</span></div>', unsafe_allow_html=True)

    st.subheader("Amount trail")
    cols = st.columns(4)
    with cols[0]: source_amount("ERP amount", case["erp"])
    with cols[1]: source_amount("Gateway amount", case["gateway"])
    with cols[2]: source_amount("Bank settled", case["bank"])
    with cols[3]:
        difference = case["erp"] - case["bank"]
        st.markdown(f'<div class="difference"><div class="source-name">Difference</div><div class="source-value">{money(difference)}</div></div>', unsafe_allow_html=True)

    st.subheader("WHY WE BELIEVE THIS")
    st.markdown(f'<div class="narrative">{case["narrative"]}</div>', unsafe_allow_html=True)

    left, right = st.columns(2)
    with left:
        st.subheader("Evidence checklist")
        st.markdown("".join(f'<div class="check"><span class="check-mark">✓</span>{item}</div>' for item in case["supporting"]), unsafe_allow_html=True)
    with right:
        st.subheader("Challenge checks")
        st.markdown("".join(f'<div class="check"><span class="check-mark">✓</span>{item}</div>' for item in case["checks"]), unsafe_allow_html=True)

    st.subheader("Alternatives considered")
    st.markdown(f'<div class="panel subtle">{case["alternatives"]}</div>', unsafe_allow_html=True)

    st.subheader("Decision")
    st.caption(f"Confidence is secondary metadata: {case['confidence']} ({case['confidence_score']}%).")
    action_cols = st.columns([1, 1, 1, 2.5])
    with action_cols[0]:
        if st.button("Approve", type="primary", use_container_width=True, key=f"approve-{case['id']}"):
            st.session_state.decisions[case["id"]] = "Approved"
    with action_cols[1]:
        if st.button("Reject", use_container_width=True, key=f"reject-{case['id']}"):
            st.session_state.decisions[case["id"]] = "Rejected"
    with action_cols[2]:
        if st.button("Challenge", use_container_width=True, key=f"challenge-{case['id']}"):
            st.session_state.decisions[case["id"]] = "Challenged"
    decision = st.session_state.decisions.get(case["id"])
    if decision:
        st.success(f"Decision recorded: {decision}. This mock action is ready to be wired to the graph.")


def main() -> None:
    st.set_page_config(page_title="Reconciliation Investigator", page_icon="◎", layout="wide", initial_sidebar_state="expanded")
    inject_styles()
    st.session_state.setdefault("selected_case", None)
    st.session_state.setdefault("decisions", {})
    with st.sidebar:
        st.markdown('<div class="eyebrow">INVESTIGATOR</div>', unsafe_allow_html=True)
        st.markdown("### Reconciliation")
        st.caption("Mock data · graph disconnected")
        view = st.radio("View", ["Overview", "Investigation"], index=1 if st.session_state.selected_case else 0)
        if st.button("Reset mock decisions", use_container_width=True):
            st.session_state.decisions = {}
            st.rerun()
    selected = next((case for case in MOCK_CASES if case["id"] == st.session_state.selected_case), None)
    if view == "Investigation" and selected:
        investigation(selected)
    else:
        dashboard(MOCK_CASES)


if __name__ == "__main__":
    main()
