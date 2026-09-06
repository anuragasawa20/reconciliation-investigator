# AI Financial Reconciliation Investigator

## Technical Specification

**Status:** MVP specification  
**Primary audience:** Engineering, product, hackathon judges, and finance stakeholders  
**Source of truth:** This document defines the intended MVP unless superseded by an explicit user requirement.

## 1. Product definition

The AI Financial Reconciliation Investigator accepts an unexplained transaction mismatch, investigates controlled root-cause hypotheses using evidence from exactly three financial systems, incorporates patterns from previously resolved cases, challenges its own conclusion, and produces an auditable explanation and recommended action.

The product is not an AI transaction matcher. Matching and arithmetic are deterministic. AI-assisted reasoning begins only after deterministic rules identify an exception.

### 1.1 Required data sources

| Source | Meaning |
|---|---|
| ERP | What the company believes happened |
| Payment Gateway | What the customer and payment processor recorded |
| Bank | What actually settled into the bank |

No live third-party integrations are required for the MVP. Mock CSV data is sufficient.

### 1.2 Hero scenario

```text
ERP amount       ₹10,000
Gateway amount   ₹10,000
Bank settlement   ₹ 9,700
Difference        ₹   300
```

Expected result:

- Root cause: Gateway Processing Fee
- Confidence band: High
- Evidence: the gateway records a 3% / ₹300 fee, the bank received ₹9,700, no refund exists, and similar historical cases share the pattern
- Explanation: ₹10,000 × 3% = ₹300 and ₹10,000 − ₹300 = ₹9,700
- Recommendation: Auto-resolve as an expected gateway fee

The explanation is the primary product output. Confidence is supporting metadata, never a substitute for evidence.

## 2. Goals and non-goals

### 2.1 MVP goals

1. Ingest ERP, gateway, and bank datasets.
2. Normalize inconsistent identifiers and schemas deterministically.
3. Match related records with transparent rules.
4. isolate exceptions before any AI-assisted processing.
5. Investigate exceptions using a bounded hypothesis library.
6. Retrieve factual evidence through deterministic tools.
7. Validate and compare hypotheses.
8. Challenge the leading conclusion with falsification checks.
9. Explain the selected root cause in finance-friendly language.
10. Recommend auto-resolution or human review.
11. Store and retrieve resolved cases as simple memory.
12. Demonstrate safe uncertainty when evidence is insufficient.

### 2.2 Explicit non-goals

- Live ERP, Stripe, payment gateway, or bank integrations
- Authentication or multi-tenant architecture
- Production-grade vector search or a complex RAG pipeline
- Fine-tuning
- Autonomous journal entries or movement of money
- More than the controlled MVP hypothesis set
- Production compliance certification
- An agent for every processing step
- LLM-based arithmetic, matching, or source-of-truth retrieval

## 3. Core design principles

### 3.1 Deterministic finance, probabilistic interpretation

Normal Python code owns:

- CSV loading and schema validation
- identifier normalization
- currency and amount parsing
- date parsing and window calculations
- record matching
- discrepancy calculations
- fee calculations
- evidence retrieval
- database reads and writes
- threshold-based routing

AI-assisted nodes may own:

- selecting hypotheses from an approved library
- deciding which available evidence is relevant
- interpreting evidence across competing hypotheses
- articulating contradictory or missing evidence
- producing a concise root-cause explanation
- phrasing an operational recommendation

### 3.2 Evidence before explanation

Every factual claim in a conclusion must trace to one of the three source datasets, deterministic derived calculations, or stored historical cases. The model must not manufacture transactions, fee rates, refund records, dates, or prior cases.

### 3.3 Bounded investigation

The investigator selects from a controlled hypothesis library. It must not create an endless or arbitrary chain of causes. The MVP hypotheses are:

- `gateway_fee`
- `refund`
- `timing_difference`
- `manual_adjustment`
- `duplicate_or_missing_transaction`
- `unknown`

### 3.4 Explainability over score

Every result must contain:

- the proposed root cause
- facts supporting it
- facts opposing it
- missing evidence, if any
- deterministic calculations
- alternatives considered and why they were rejected
- a plain-language conclusion
- an action recommendation
- a confidence band and optional numeric score

### 3.5 Safe abstention

When no hypothesis meets the configured evidence and confidence threshold, the system must return `unknown` and require human review. It must never auto-resolve solely because one weak hypothesis scored higher than other weak hypotheses.

## 4. Data contracts

All monetary values must be stored internally as integer minor units or exact decimals. Binary floating-point values must not be used for financial calculations.

### 4.1 ERP input

File: `data/erp_transactions.csv`

| Field | Type | Required | Description |
|---|---:|---:|---|
| `transaction_id` | string | yes | ERP transaction identifier |
| `invoice_id` | string | yes | Invoice identifier |
| `amount` | decimal | yes | Expected gross amount |
| `currency` | ISO 4217 string | yes | Transaction currency |
| `date` | ISO date | yes | ERP transaction date |
| `status` | string | yes | ERP status |

### 4.2 Gateway input

File: `data/gateway_transactions.csv`

| Field | Type | Required | Description |
|---|---:|---:|---|
| `transaction_id` | string | yes | Gateway payment identifier |
| `invoice_id` | string | yes | Referenced invoice identifier |
| `amount` | decimal | yes | Gateway gross amount |
| `currency` | ISO 4217 string | yes | Transaction currency |
| `date` | ISO date | yes | Gateway transaction date |
| `status` | string | yes | Gateway status |
| `fee` | decimal | no | Recorded processing fee |
| `fee_rate` | decimal | no | Configured or recorded fee rate |
| `settlement_id` | string | no | Gateway settlement identifier |
| `refund_amount` | decimal | no | Recorded refund amount, if present |

### 4.3 Bank input

File: `data/bank_transactions.csv`

| Field | Type | Required | Description |
|---|---:|---:|---|
| `transaction_id` | string | yes | Bank transaction identifier |
| `settlement_id` | string | no | Settlement reference |
| `amount` | decimal | yes | Bank transaction amount |
| `settled_amount` | decimal | yes | Amount that settled |
| `currency` | ISO 4217 string | yes | Settlement currency |
| `date` | ISO date | yes | Bank record date |
| `settlement_date` | ISO date | yes | Effective settlement date |
| `status` | string | yes | Bank status |

### 4.4 Canonical transaction

```json
{
  "invoice_id": "INV-1045",
  "erp_transaction_id": "erp_1045",
  "gateway_transaction_id": "payment_78321",
  "bank_transaction_id": "bank_991",
  "settlement_id": "settlement_991",
  "currency": "INR",
  "expected_amount": "10000.00",
  "gateway_amount": "10000.00",
  "gateway_fee": "300.00",
  "bank_amount": "9700.00",
  "transaction_date": "2026-09-01",
  "settlement_date": "2026-09-03"
}
```

### 4.5 Match result

```json
{
  "invoice_id": "INV-1045",
  "match_status": "PARTIAL_MATCH",
  "gross_difference": "0.00",
  "settlement_difference": "300.00",
  "matched_by": ["invoice_id", "settlement_id"],
  "warnings": []
}
```

Allowed match statuses:

- `MATCHED`
- `PARTIAL_MATCH`
- `MISMATCH`
- `UNMATCHED`
- `AMBIGUOUS`

### 4.6 Investigation result

```json
{
  "case_id": "CASE-INV-1045",
  "invoice_id": "INV-1045",
  "root_cause": "gateway_fee",
  "confidence": {
    "score": 0.96,
    "band": "HIGH"
  },
  "evidence_for": [
    {
      "fact": "Gateway fee",
      "value": "300.00",
      "source": "gateway",
      "source_record_id": "payment_78321"
    }
  ],
  "evidence_against": [],
  "missing_evidence": [],
  "calculations": [
    "10000.00 * 0.03 = 300.00",
    "10000.00 - 300.00 = 9700.00"
  ],
  "alternatives": [
    {
      "hypothesis": "refund",
      "status": "NOT_SUPPORTED",
      "reason": "No refund record was found."
    }
  ],
  "explanation": "The gateway's recorded fee exactly explains the bank shortfall.",
  "recommendation": {
    "resolution": "Expected Gateway Fee",
    "action": "AUTO_RESOLVE",
    "human_intervention_required": false
  },
  "audit": {
    "rules_version": "1.0.0",
    "investigated_at": "2026-09-06T12:00:00Z"
  }
}
```

## 5. System architecture

```text
CSV data
   |
   v
Loader -> Validator -> Normalizer -> Matcher -> Exception Detector
                                                |             |
                                             matched       exception
                                                |             |
                                               END            v
                                                    Investigation Planner
                                                             |
                                          Historical Case Retrieval
                                                             |
                                               Controlled Hypotheses
                                                             |
                                                Evidence Collection
                                                             |
                                               Hypothesis Validation
                                                             |
                                                     Challenger
                                                             |
                                              Root Cause Selection
                                                             |
                                                  Recommendation
                                                             |
                                                Store Case Memory
                                                             |
                                                            END
```

The workflow may be represented with LangGraph, but most nodes are ordinary Python functions. LangGraph coordinates state and routing; it does not replace deterministic business logic.

### 5.1 Suggested repository structure

```text
financial-reconciler/
|-- AGENTS.md
|-- TECHNICAL_SPEC.md
|-- data/
|   |-- erp_transactions.csv
|   |-- gateway_transactions.csv
|   |-- bank_transactions.csv
|   `-- historical_cases.db
|-- core/
|   |-- loader.py
|   |-- normalizer.py
|   |-- matcher.py
|   `-- exceptions.py
|-- agents/
|   |-- planner.py
|   |-- validator.py
|   |-- challenger.py
|   `-- recommendation.py
|-- tools/
|   |-- gateway_tools.py
|   |-- bank_tools.py
|   |-- refund_tools.py
|   `-- memory_tools.py
|-- graph/
|   `-- reconciliation_graph.py
|-- ui/
|   `-- app.py
|-- tests/
`-- main.py
```

## 6. Component specifications

### 6.1 Data ingestion

Responsibilities:

- Load the three approved datasets.
- Validate required columns, types, and supported currencies.
- Reject malformed monetary or date values with row-level errors.
- Preserve raw record identifiers for the audit trail.
- Report counts of accepted and rejected rows.

The loader must not silently coerce invalid values into zero, empty strings, or current dates.

### 6.2 Normalization

Responsibilities:

- Standardize invoice identifiers, for example `INV1045` to `INV-1045`.
- Trim and case-normalize identifiers without destroying the raw value.
- Parse dates to a single internal representation.
- Parse amounts to exact decimal or integer minor units.
- Normalize currency codes.
- Produce canonical records with source lineage.

Normalization is deterministic and versioned.

### 6.3 Matching engine

Matching proceeds from strongest to weakest identifiers:

1. Exact settlement identifier.
2. Exact normalized invoice identifier.
3. Exact transaction or cross-reference identifier.
4. Currency + amount + configured date window.

The engine must:

- score rules transparently without AI inference;
- reject cross-currency matches unless an explicit FX record exists;
- mark multiple equally valid candidates as `AMBIGUOUS`;
- preserve the rules responsible for each link;
- compare ERP to gateway, gateway to bank, and ERP to bank.

Default settlement date window: two business days. This value must be configurable.

### 6.4 Exception detector

Only exceptions enter the investigation workflow. A record is an exception when any of the following apply:

- expected and settled amounts differ outside the configured tolerance;
- a source record is missing;
- multiple source records compete for one canonical transaction;
- currency differs;
- status combinations are inconsistent;
- settlement occurs outside the allowed window.

Default amount tolerance: `0.01` in the transaction currency.

### 6.5 Investigation planner

Input: one exception plus relevant canonical records.  
Output: an ordered subset of the controlled hypothesis library.

The planner may use AI-assisted reasoning, but its output must validate against the approved enum. It must include `unknown` as the fallback and may not recursively create new hypotheses.

### 6.6 Historical case retrieval

Use SQLite for MVP memory. Suggested table:

```sql
CREATE TABLE reconciliation_cases (
    case_id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    difference_minor INTEGER NOT NULL,
    pattern TEXT NOT NULL,
    resolution TEXT NOT NULL,
    confidence REAL NOT NULL,
    case_summary TEXT NOT NULL,
    human_override TEXT,
    created_at TEXT NOT NULL
);
```

Similarity may be deterministic for the MVP, based on:

- same currency;
- same difference ratio band;
- same fee-rate band;
- same status combination;
- same missing/present source pattern;
- same final pattern.

Historical cases are supporting evidence only. They cannot override contradictory current transaction evidence.

### 6.7 Evidence tools

The evidence collector calls functions and returns structured facts. It does not decide whether a hypothesis is true.

Required tool interfaces:

```python
get_gateway_transaction(transaction_id=None, invoice_id=None)
get_fee_configuration(gateway_transaction_id)
get_bank_settlement(settlement_id)
get_refund_record(gateway_transaction_id)
get_related_transactions(invoice_id)
search_historical_cases(case_features, limit=5)
```

Every returned fact must include:

- fact name;
- typed value;
- source system;
- source record identifier;
- retrieval status;
- optional derivation rule.

`NOT_FOUND` is evidence. It must be distinguished from `SOURCE_UNAVAILABLE` and from a malformed source record.

### 6.8 Hypothesis validator

For each hypothesis, the validator produces:

```json
{
  "hypothesis": "gateway_fee",
  "evidence_for": [],
  "evidence_against": [],
  "missing_evidence": [],
  "reasoning": [],
  "calculations": [],
  "conclusion": "SUPPORTED",
  "confidence": 0.96
}
```

Allowed conclusions:

- `SUPPORTED`
- `PARTIALLY_SUPPORTED`
- `NOT_SUPPORTED`
- `INSUFFICIENT_EVIDENCE`

Calculations are supplied by deterministic functions, not generated freehand by the model.

### 6.9 Challenger

The challenger attempts to falsify the leading hypothesis. For a gateway fee, it checks:

- Is the fee explicitly recorded?
- Does the recorded or configured fee rate reproduce the fee amount?
- Does gross amount minus fee equal the settled amount within tolerance?
- Is there a refund that explains some or all of the difference?
- Is settlement outside the normal time window?
- Are there duplicate or missing records?
- Does current evidence contradict historical patterns?

The challenger returns checks performed, contradictory evidence, unresolved questions, and an adjusted confidence. It may lower confidence or force human review. It must not increase confidence solely because no contradictory record was found when a source was unavailable.

### 6.10 Root-cause determination

The root-cause node selects the strongest supported hypothesis only after challenge checks. Default policy:

- `HIGH`: score at least `0.90`, all required evidence present, no material contradiction
- `MEDIUM`: score from `0.70` to `<0.90`, or a non-material evidence gap
- `LOW`: score below `0.70`

Auto-resolution requires `HIGH` confidence, a permitted resolution type, available source records, and no material contradiction. All other cases require human review.

Thresholds are configuration, not prompts.

### 6.11 Recommendation

Allowed MVP actions:

- `AUTO_RESOLVE`
- `HUMAN_REVIEW`
- `WAIT_FOR_SETTLEMENT`
- `REQUEST_MISSING_EVIDENCE`

The recommendation must state:

- resolution label;
- action;
- why the action is safe or necessary;
- whether human intervention is required;
- the exact missing item when evidence is incomplete.

### 6.12 Memory write

Only finalized cases are stored. Auto-resolved cases record the system decision. Human-reviewed cases record both the proposed decision and the human override. Historical memory must preserve provenance and must not be silently rewritten.

## 7. Explainability contract

The user interface must lead with the explanation, not the numeric confidence.

Required presentation order:

1. Root cause
2. Why we believe this
3. Supporting evidence
4. Contradicting evidence or explicit statement that none was found
5. Alternatives considered
6. Conclusion
7. Recommended action
8. Confidence band and score as secondary metadata

Example:

```text
ROOT CAUSE
Gateway Processing Fee

WHY WE BELIEVE THIS
ERP and gateway both record a gross amount of ₹10,000. The bank received
₹9,700. The gateway records a 3% processing fee.

₹10,000 × 3% = ₹300
₹10,000 − ₹300 = ₹9,700

The fee therefore explains the entire discrepancy.

SUPPORTING EVIDENCE
✓ Gateway fee = ₹300
✓ Configured fee rate = 3%
✓ Bank settlement = ₹9,700
✓ Fourteen resolved cases show the same pattern

CHALLENGE CHECKS
✓ No refund was found
✓ No duplicate was found
✓ Settlement occurred within the normal window

CONCLUSION
This is an expected processing fee, not a financial error.

RECOMMENDED ACTION
Auto-resolve as Expected Gateway Fee.

CONFIDENCE
High (96%)
```

The explanation must use only evidence present in the structured investigation result. Unsupported prose is a validation failure.

## 8. Workflow state

Suggested graph state:

```python
class ReconciliationState(TypedDict):
    raw_records: dict
    canonical_transaction: dict | None
    match_result: dict | None
    exception: dict | None
    historical_cases: list[dict]
    hypotheses: list[str]
    evidence: dict[str, list[dict]]
    validations: list[dict]
    challenge: dict | None
    root_cause: str | None
    explanation: dict | None
    recommendation: dict | None
    errors: list[dict]
```

Routing rules:

- `MATCHED` routes directly to `END`.
- Any exception routes to planning and evidence collection.
- Source failure routes to `REQUEST_MISSING_EVIDENCE` unless remaining evidence is independently sufficient under policy.
- No post-challenge hypothesis at or above `0.70` routes to `HUMAN_REVIEW` with root cause `unknown`.
- Only policy-approved high-confidence results route to `AUTO_RESOLVE`.

## 9. MVP scenarios and seed data

Generate approximately 50 transactions. The deterministic engine should yield approximately 40–45 matched records and 5–10 exceptions.

The seed data must include these patterns:

| Scenario | Evidence pattern | Expected result |
|---|---|---|
| Gateway fee | Gross ERP and gateway match; bank equals gross minus recorded fee | High confidence; auto-resolve |
| Refund | Gateway refund plus settlement explains difference | Supported refund; resolve or review per policy |
| Settlement delay | No bank settlement yet; date remains inside expected window | Wait for settlement |
| Duplicate | More than one candidate transaction or duplicate settlement | Human review |
| Unknown | No approved hypothesis sufficiently explains the difference | Human review |

The demo must show three cases:

1. Easy win: gateway fee, high confidence, auto-resolve.
2. Confirmed investigation: refund evidence explains the discrepancy.
3. Hard case: competing or insufficient evidence, safe human-review escalation.

## 10. User interface specification

Use one Streamlit dashboard.

### 10.1 Summary

Display:

- total transactions;
- exception count;
- auto-resolved count;
- human-review count.

### 10.2 Exception table

Columns:

- case / invoice identifier;
- discrepancy amount;
- root-cause or investigation status;
- recommended action.

### 10.3 Investigation view

Display:

- ERP gross amount;
- gateway gross amount;
- bank settlement amount;
- discrepancy;
- ranked hypotheses;
- evidence with source labels;
- challenger checks;
- root-cause explanation;
- rejected alternatives;
- recommended action;
- confidence as secondary metadata.

The hard-case screen must make missing evidence and the reason for human review obvious.

## 11. Auditability, safety, and failure behavior

- Preserve raw and normalized identifiers.
- Record the version of normalization and matching rules.
- Record timestamps for investigation and resolution.
- Distinguish missing evidence from unavailable systems.
- Never treat absence from an unavailable source as proof that an event did not occur.
- Never auto-resolve cross-currency discrepancies without explicit FX evidence.
- Never execute accounting entries or payments in the MVP.
- Store both proposed and human-overridden outcomes.
- Keep logs free of credentials and unnecessary customer data.
- Validate AI node output against typed schemas before routing.
- On invalid model output, retry once with schema correction, then route to human review.

## 12. Testing strategy

### 12.1 Unit tests

- Identifier normalization variants
- Exact decimal arithmetic
- Date-window boundaries
- Each matching rule and precedence order
- Ambiguous match detection
- Exception thresholds
- Each evidence tool's `FOUND`, `NOT_FOUND`, and `SOURCE_UNAVAILABLE` behavior
- Confidence band and action routing

### 12.2 Integration tests

- Three CSV files produce the expected canonical transactions.
- Matched transactions never invoke investigation nodes.
- Fee evidence produces the exact deterministic equations.
- Refund and fee hypotheses are compared without double-counting evidence.
- Challenger lowers confidence when contradictory evidence exists.
- Unknown cases route to human review.
- Resolved cases are written and retrieved from SQLite.

### 12.3 End-to-end acceptance tests

#### Gateway fee

Given ERP and gateway amounts of ₹10,000, a recorded fee of ₹300 / 3%, and a bank settlement of ₹9,700, the system must explain both equations, show source-backed evidence, rule out the configured alternatives, and recommend auto-resolution.

#### Refund

Given a refund record that exactly explains the shortfall, the system must identify the refund, cite the refund record, and avoid classifying it as a fee.

#### Uncertain case

Given incomplete or contradictory evidence and no hypothesis at or above the decision threshold, the system must return `unknown`, identify what is missing, and recommend human review.

## 13. Definition of done

The MVP is complete when:

- all three mock datasets load and validate;
- normalization and matching are deterministic and tested;
- only exceptions enter the investigation workflow;
- the controlled hypothesis library is enforced;
- every displayed fact has source lineage;
- validator output includes evidence for, evidence against, reasoning, and calculations;
- the challenger can reduce confidence or force human review;
- the final explanation is generated exclusively from structured evidence;
- high-confidence permitted cases auto-resolve;
- uncertain cases visibly abstain and require human review;
- resolved cases persist to SQLite and can support later investigations;
- the three required demo scenarios run end-to-end;
- the dashboard prioritizes explanation over confidence.

## 14. Ten-hour implementation plan

### Hour 0–1: schema and dataset

- Create ERP, gateway, bank, and historical case data.
- Generate approximately 50 transactions.
- Seed gateway fee, refund, delay, duplicate, and unknown exceptions.

Exit condition: inputs validate and expected scenario counts are documented.

### Hour 1–2: deterministic reconciliation

- Implement loader, normalizer, matcher, and exception detector.

Exit condition: matched records and exceptions are separated correctly.

### Hour 2–3: workflow skeleton

- Define graph state and routing.
- Implement planner, validator, challenger, recommendation, and memory nodes as minimal functions.

Exit condition: one case runs from input to recommendation.

### Hour 3–5: evidence and hypothesis system

- Implement source-backed evidence tools.
- Evaluate the bounded hypothesis set.
- Add typed outputs and deterministic calculations.

Exit condition: mismatch, hypotheses, evidence, validation, and winner are visible.

### Hour 5–6: memory

- Add SQLite case storage and deterministic similarity retrieval.

Exit condition: prior cases can be stored and surfaced with provenance.

### Hour 6–7: challenger and explanations

- Add falsification checks, contradictory evidence, missing evidence, rejected alternatives, and final narrative generation.

Exit condition: the explanation contains only traceable facts and uncertain cases abstain.

### Hour 7–8.5: dashboard

- Build summary metrics, exception table, and investigation view in Streamlit.

Exit condition: a user can inspect all three demo scenarios from one page.

### Hour 8.5–9.5: demo hardening

- Rehearse gateway-fee, refund, and unknown scenarios.
- Fix misleading states, labels, or routing.

Exit condition: each scenario produces its expected outcome reproducibly.

### Hour 9.5–10: verification and pitch polish

- Run tests and a clean local startup.
- Confirm audit details and source labels.
- Prepare the concise product story.

## 15. Product narrative

Financial teams do not merely have a matching problem. They have an investigation problem.

This system:

1. Detects a mismatch deterministically.
2. Generates bounded possible causes.
3. Collects facts from financial systems.
4. Tests competing hypotheses.
5. Challenges its own leading conclusion.
6. Explains the root cause with traceable evidence.
7. Resolves automatically only when policy allows.
8. Learns from finalized human and system decisions.

The result is an auditable reconciliation investigator: deterministic where correctness is mechanical, AI-assisted where interpretation is valuable, and explicitly uncertain when the evidence is not strong enough.
