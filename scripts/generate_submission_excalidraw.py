#!/usr/bin/env python3
"""Generate the hackathon submission architecture as an Excalidraw file."""

from __future__ import annotations

import json
from pathlib import Path

SEED = 1
ELEMENTS: list[dict] = []


def nid(prefix: str) -> str:
    global SEED
    SEED += 1
    return f"{prefix}{SEED:04d}"


def base(**kwargs):
    el = {
        "id": kwargs.pop("id"),
        "x": 0,
        "y": 0,
        "width": 0,
        "height": 0,
        "angle": 0,
        "strokeColor": "#1e1e1e",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": None,
        "seed": SEED,
        "version": 1,
        "versionNonce": SEED,
        "isDeleted": False,
        "boundElements": None,
        "updated": 1,
        "link": None,
        "locked": False,
    }
    el.update(kwargs)
    return el


def bind_text(container: dict, text_el: dict) -> None:
    container["boundElements"] = (container.get("boundElements") or []) + [
        {"id": text_el["id"], "type": "text"}
    ]
    text_el["containerId"] = container["id"]


def bind_arrow(shape: dict, arrow_id: str) -> None:
    shape["boundElements"] = (shape.get("boundElements") or []) + [
        {"id": arrow_id, "type": "arrow"}
    ]


def rect(x, y, w, h, *, fill="#e7f5ff", stroke="#1c7ed6", roundness=True, **kw):
    el = base(
        id=nid("r"),
        type="rectangle",
        x=x,
        y=y,
        width=w,
        height=h,
        backgroundColor=fill,
        strokeColor=stroke,
        roundness={"type": 3} if roundness else None,
        **kw,
    )
    ELEMENTS.append(el)
    return el


def diamond(x, y, w, h, *, fill="#fff3bf", stroke="#f08c00"):
    el = base(
        id=nid("d"),
        type="diamond",
        x=x,
        y=y,
        width=w,
        height=h,
        backgroundColor=fill,
        strokeColor=stroke,
        roundness={"type": 2},
    )
    ELEMENTS.append(el)
    return el


def ellipse(x, y, w, h, *, fill="#d3f9d8", stroke="#2b8a3e"):
    el = base(
        id=nid("e"),
        type="ellipse",
        x=x,
        y=y,
        width=w,
        height=h,
        backgroundColor=fill,
        strokeColor=stroke,
    )
    ELEMENTS.append(el)
    return el


def label(
    text: str,
    x,
    y,
    w,
    h,
    *,
    size=16,
    align="center",
    valign="middle",
    color="#1e1e1e",
    container=None,
    family=2,
):
    el = base(
        id=nid("t"),
        type="text",
        x=x,
        y=y,
        width=w,
        height=h,
        text=text,
        originalText=text,
        fontSize=size,
        fontFamily=family,
        textAlign=align,
        verticalAlign=valign,
        strokeColor=color,
        backgroundColor="transparent",
        strokeWidth=1,
        containerId=container["id"] if container else None,
        autoResize=True,
        lineHeight=1.25,
    )
    ELEMENTS.append(el)
    if container:
        bind_text(container, el)
    return el


def boxed(text, x, y, w, h, *, fill, stroke, size=16, color="#1e1e1e", roundness=True, sw=2):
    box = rect(x, y, w, h, fill=fill, stroke=stroke, roundness=roundness, strokeWidth=sw)
    label(text, x + 8, y + 8, w - 16, h - 16, size=size, color=color, container=box)
    return box


def titled(title, body, x, y, w, h, *, fill, stroke, title_size=16, body_size=13):
    gid = nid("g")
    box = rect(x, y, w, h, fill=fill, stroke=stroke)
    t1 = label(title, x + 10, y + 10, w - 20, 28, size=title_size, container=None, align="left", valign="top")
    t2 = label(body, x + 10, y + 40, w - 20, h - 50, size=body_size, align="left", valign="top")
    for el in (box, t1, t2):
        el["groupIds"] = [gid]
    return box


def arrow(start, end, *, label_text=None, color="#495057", dashed=False, points=None):
    sx, sy = start
    ex, ey = end
    aid = nid("a")
    pts = points or [[0, 0], [ex - sx, ey - sy]]
    el = base(
        id=aid,
        type="arrow",
        x=sx,
        y=sy,
        width=abs(ex - sx) or 1,
        height=abs(ey - sy) or 1,
        strokeColor=color,
        strokeWidth=2,
        strokeStyle="dashed" if dashed else "solid",
        roundness={"type": 2},
        points=pts,
        lastCommittedPoint=None,
        startBinding=None,
        endBinding=None,
        startArrowhead=None,
        endArrowhead="arrow",
        elbowed=False,
    )
    ELEMENTS.append(el)
    if label_text:
        mx = (sx + ex) / 2
        my = (sy + ey) / 2 - 18
        label(label_text, mx - 70, my, 140, 22, size=12, color=color)
    return el


def connected_arrow(a, b, *, side="h", label_text=None, color="#495057", dashed=False, gap=6):
    if side == "h":
        start = (a["x"] + a["width"] + gap, a["y"] + a["height"] / 2)
        end = (b["x"] - gap, b["y"] + b["height"] / 2)
    elif side == "v":
        start = (a["x"] + a["width"] / 2, a["y"] + a["height"] + gap)
        end = (b["x"] + b["width"] / 2, b["y"] - gap)
    elif side == "up":
        start = (a["x"] + a["width"] / 2, a["y"] - gap)
        end = (b["x"] + b["width"] / 2, b["y"] + b["height"] + gap)
    else:
        start = (a["x"] + a["width"] / 2, a["y"] + a["height"] + gap)
        end = (b["x"] + b["width"] / 2, b["y"] - gap)
    el = arrow(start, end, label_text=label_text, color=color, dashed=dashed)
    bind_arrow(a, el["id"])
    bind_arrow(b, el["id"])
    el["startBinding"] = {"elementId": a["id"], "focus": 0, "gap": gap}
    el["endBinding"] = {"elementId": b["id"], "focus": 0, "gap": gap}
    return el


def legend_swatch(x, y, fill, stroke, text):
    boxed("", x, y, 22, 22, fill=fill, stroke=stroke, size=8)
    label(text, x + 30, y, 220, 22, size=13, align="left")


def main() -> None:
    canvas = rect(-40, -40, 2480, 2100, fill="#ffffff", stroke="#ffffff", roundness=False, strokeWidth=0)
    canvas["opacity"] = 0

    title_box = boxed(
        "AI Financial Reconciliation Investigator",
        40,
        20,
        1680,
        56,
        fill="#edf2ff",
        stroke="#3b5bdb",
        size=28,
        sw=3,
    )
    label(
        "Hackathon submission  ·  what this repo actually runs",
        40,
        82,
        1680,
        28,
        size=16,
        color="#495057",
        align="left",
    )

    hero = titled(
        "Hero case  INV-1045",
        "ERP ₹10,000\nGateway ₹10,000\nBank ₹9,700\nDiff ₹300  →  gateway_fee\nHIGH  ·  AUTO_RESOLVE",
        1780,
        20,
        620,
        170,
        fill="#fff4e6",
        stroke="#e8590c",
        title_size=18,
        body_size=15,
    )

    principle = boxed(
        "Rule: Python owns money, matching, and retrieval.  AI may rank hypotheses and write the explanation — it never invents amounts, fees, refunds, or history.",
        40,
        118,
        1680,
        52,
        fill="#fff9db",
        stroke="#f08c00",
        size=15,
    )

    # --- Sources ---
    sources_frame = rect(40, 200, 2360, 170, fill="#f8f9fa", stroke="#ced4da")
    label("1. Three mock financial systems  (CSV only — no live Stripe / ERP / bank APIs)", 56, 210, 1400, 28, size=18, align="left", color="#212529")

    erp = titled(
        "ERP",
        "What the company booked\ndata/erp_transactions.csv\ninvoice, amount, date, status",
        70,
        250,
        720,
        100,
        fill="#d0ebff",
        stroke="#1c7ed6",
    )
    gw = titled(
        "Payment gateway",
        "What the processor recorded\ndata/gateway_transactions.csv\namount, fee, fee_rate, refund, settlement_id",
        830,
        250,
        760,
        100,
        fill="#d3f9d8",
        stroke="#2b8a3e",
    )
    bank = titled(
        "Bank",
        "What actually settled\ndata/bank_transactions.csv\nsettled_amount, settlement_date, settlement_id",
        1630,
        250,
        740,
        100,
        fill="#ffe3e3",
        stroke="#c92a2a",
    )

    # --- Deterministic core ---
    det_frame = rect(40, 400, 2360, 250, fill="#e7f5ff", stroke="#1971c2")
    label("2. Deterministic reconciliation  ·  core/  ·  no LLM", 56, 410, 900, 28, size=18, align="left", color="#1864ab")

    load = boxed("Loader\nvalidate columns\nreject bad money/dates", 70, 455, 280, 90, fill="#ffffff", stroke="#1971c2", size=15)
    norm = boxed("Normalizer\nINV1045 → INV-1045\nDecimal amounts", 390, 455, 280, 90, fill="#ffffff", stroke="#1971c2", size=15)
    match = boxed("Matcher\nsettlement_id → invoice\n→ ids → amount+window", 710, 455, 300, 90, fill="#ffffff", stroke="#1971c2", size=15)
    exc = boxed("Exception detector\ntolerance 0.01\nmissing / ambiguous / FX", 1050, 455, 300, 90, fill="#ffffff", stroke="#1971c2", size=15)

    connected_arrow(load, norm)
    connected_arrow(norm, match)
    connected_arrow(match, exc)

    matched = boxed("MATCHED\nstop here", 1410, 455, 200, 90, fill="#d3f9d8", stroke="#2b8a3e", size=16)
    exception = boxed("EXCEPTION\nenter investigator", 1660, 455, 240, 90, fill="#ffe3e3", stroke="#c92a2a", size=16)
    statuses = boxed(
        "Match statuses\nMATCHED  PARTIAL_MATCH\nMISMATCH  UNMATCHED  AMBIGUOUS",
        1950,
        455,
        410,
        90,
        fill="#ffffff",
        stroke="#1971c2",
        size=14,
    )
    connected_arrow(exc, matched, label_text="clean")
    connected_arrow(exc, exception, label_text="break")

    label(
        "Matching is scored by transparent rules. Matched records never invoke planner / validator / challenger.",
        70,
        560,
        1800,
        28,
        size=14,
        align="left",
        color="#1864ab",
    )
    label(
        "Implemented in core/loader.py, core/normalizer.py, core/matcher.py, core/exceptions.py  ·  load_exception node runs this for one invoice",
        70,
        592,
        2000,
        28,
        size=13,
        align="left",
        color="#495057",
    )

    # --- LangGraph ---
    graph_frame = rect(40, 680, 2360, 380, fill="#fff4e6", stroke="#e8590c")
    label("3. LangGraph investigation  ·  graph/reconciliation_graph.py  ·  SqliteSaver checkpoints", 56, 690, 1600, 28, size=18, align="left", color="#d9480f")

    n_load = boxed("load_exception", 70, 740, 210, 70, fill="#d0ebff", stroke="#1971c2", size=16)
    n_plan = boxed("plan\nLLM or deterministic\nbounded taxonomy", 310, 730, 250, 90, fill="#ffe8cc", stroke="#e8590c", size=14)
    n_ev = boxed("collect_evidence\ndeterministic tools", 590, 730, 250, 90, fill="#d0ebff", stroke="#1971c2", size=14)
    n_val = boxed("validate\nLLM + Decimal math", 870, 730, 240, 90, fill="#ffe8cc", stroke="#e8590c", size=14)
    n_ch = boxed("challenge\nfalsify the leader", 1140, 730, 230, 90, fill="#ffe8cc", stroke="#e8590c", size=14)
    n_rc = boxed("root_cause\npolicy gate", 1400, 730, 210, 90, fill="#e5dbff", stroke="#7048e8", size=14)

    connected_arrow(n_load, n_plan)
    connected_arrow(n_plan, n_ev)
    connected_arrow(n_ev, n_val)
    connected_arrow(n_val, n_ch)
    connected_arrow(n_ch, n_rc)

    retry = boxed("retry challenge\nmax 2 on schema error", 1140, 850, 230, 56, fill="#fff9db", stroke="#f08c00", size=13)
    connected_arrow(n_ch, retry, side="v", dashed=True, color="#f08c00")

    auto = boxed("AUTO_RESOLVE\nor WAIT_FOR_SETTLEMENT", 1660, 730, 280, 90, fill="#d3f9d8", stroke="#2b8a3e", size=14)
    review = boxed("review\nLangGraph interrupt()", 1980, 730, 280, 90, fill="#ffe3e3", stroke="#c92a2a", size=14)
    apply = boxed("apply_resolution\ncase_status audit row", 1660, 850, 280, 70, fill="#d0ebff", stroke="#1971c2", size=14)
    memory = boxed("store_case_memory\nSQLite historical cases", 1980, 850, 280, 70, fill="#d0ebff", stroke="#1971c2", size=14)

    connected_arrow(n_rc, auto, label_text="HIGH + policy")
    connected_arrow(n_rc, review, label_text="else")
    connected_arrow(auto, apply, side="v")
    connected_arrow(review, apply, side="v")
    connected_arrow(apply, memory)

    titled(
        "Decision policy  (config.py, not a prompt)",
        "HIGH ≥ 0.90 + required evidence + no material contradiction\nMEDIUM 0.70–<0.90  ·  LOW < 0.70\nAUTO_RESOLVE also needs ≥2 facts and amount ≤ ₹500\nNo hypothesis ≥ 0.70  →  unknown_other + HUMAN_REVIEW\nInvalid LLM JSON: retry once, then human review",
        70,
        850,
        1040,
        180,
        fill="#ffffff",
        stroke="#7048e8",
        title_size=16,
        body_size=14,
    )

    hyp_frame = rect(40, 1090, 2360, 300, fill="#fff9db", stroke="#f08c00")
    label(
        "Hypotheses this process actually considers  ·  schemas.HYPOTHESIS_TAXONOMY",
        56,
        1102,
        1800,
        28,
        size=18,
        align="left",
        color="#e67700",
    )
    label(
        "The planner may open only this enum. It ranks a live subset from evidence and history, then always appends unknown_other. No free-form causes.",
        56,
        1134,
        2280,
        24,
        size=14,
        align="left",
        color="#495057",
    )
    hypotheses = [
        ("1. gateway_fee", "Recorded gateway fee, or a bank shortfall plus historical fee cases."),
        ("2. refund", "Recorded refund, or a shortfall that a fee does not fully explain."),
        ("3. timing_difference", "Bank missing, or settlement outside the allowed window."),
        ("4. manual_adjustment", "One-to-one amount mismatch with no fee or refund explanation."),
        ("5. duplicate_or_missing_transaction", "Duplicate candidates or a missing source record."),
        ("6. unknown_other", "Always kept as fallback. Used when nothing else meets the evidence bar."),
    ]
    hyp_y = 1170
    hyp_w = 370
    hyp_gap = 16
    for index, (title, body) in enumerate(hypotheses):
        x = 70 + index * (hyp_w + hyp_gap)
        fill = "#ffe3e3" if title.startswith("6.") else "#ffffff"
        stroke = "#c92a2a" if title.startswith("6.") else "#f08c00"
        titled(title, body, x, hyp_y, hyp_w, 190, fill=fill, stroke=stroke, title_size=15, body_size=13)

    tools_y = 1420
    tools_frame = rect(40, tools_y, 760, 470, fill="#ebfbee", stroke="#2b8a3e")
    label("4. Evidence tools  ·  tools/", 56, tools_y + 12, 500, 26, size=18, align="left", color="#2b8a3e")
    titled(
        "Lookups return facts, not verdicts",
        "get_gateway_transaction\nget_fee_configuration\nget_bank_settlement\nget_refund_record\nget_related_transactions\nsearch_historical_cases\n\nEach fact has source, record id,\nvalue, retrieval status.\nNOT_FOUND ≠ SOURCE_UNAVAILABLE\n≠ invented missing event.",
        60,
        tools_y + 50,
        720,
        390,
        fill="#ffffff",
        stroke="#2b8a3e",
        title_size=16,
        body_size=15,
    )

    llm_frame = rect(830, tools_y, 760, 470, fill="#fff0f6", stroke="#c2255c")
    label("5. AI-assisted nodes  ·  agents/", 846, tools_y + 12, 520, 26, size=18, align="left", color="#c2255c")
    titled(
        "OpenRouter  ·  ChatOpenAI  ·  structured JSON",
        "planner.py     rank approved hypotheses\nvalidator.py   interpret evidence vs each cause\nchallenger.py  try to falsify the leader\nrecommendation.py  phrase action + explanation\n\nCalculations come from Decimal functions.\nOutputs validated against typed schemas.\nLLM can be disabled; deterministic fallbacks remain.\nLive trace: agents/trace.py → Streamlit path.",
        850,
        tools_y + 50,
        720,
        390,
        fill="#ffffff",
        stroke="#c2255c",
        title_size=16,
        body_size=15,
    )

    ui_frame = rect(1620, tools_y, 780, 470, fill="#f3f0ff", stroke="#7048e8")
    label("6. Streamlit dashboard  ·  ui/app.py", 1636, tools_y + 12, 560, 26, size=18, align="left", color="#5f3dc4")
    titled(
        "Explanation first, confidence last",
        "Exception table + live agent path\nERP / gateway / bank amounts\nRanked hypotheses + source-backed facts\nChallenge checks + rejected alternatives\nRecommended action + human interrupt\n\nDemo cases the product must show:\n1. Easy win — gateway fee, auto-resolve\n2. Refund — evidence explains shortfall\n3. Hard case — abstain, human review\n\nMemory: data/historical_cases.db + state.db",
        1640,
        tools_y + 50,
        740,
        390,
        fill="#ffffff",
        stroke="#7048e8",
        title_size=16,
        body_size=15,
    )

    legend_y = 1920
    legend_swatch(40, legend_y, "#d0ebff", "#1971c2", "Deterministic Python")
    legend_swatch(320, legend_y, "#ffe8cc", "#e8590c", "AI-assisted (schema-bound)")
    legend_swatch(640, legend_y, "#e5dbff", "#7048e8", "Policy / human control")
    legend_swatch(940, legend_y, "#d3f9d8", "#2b8a3e", "Safe resolve / wait")
    legend_swatch(1200, legend_y, "#ffe3e3", "#c92a2a", "Escalate / unknown_other")

    payload = {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": ELEMENTS,
        "appState": {
            "gridSize": None,
            "viewBackgroundColor": "#ffffff",
        },
        "files": {},
    }
    out = Path(__file__).resolve().parents[1] / "docs" / "submission-architecture.excalidraw"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out} ({len(ELEMENTS)} elements)")


if __name__ == "__main__":
    main()
