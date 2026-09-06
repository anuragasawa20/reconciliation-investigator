# Deterministic reconciliation fixtures

Run `python3 data/generate_dataset.py` to regenerate the fixtures. The default
output is 600 ERP and gateway rows, 581 bank rows, and a SQLite database with
seeded rows in `reconciliation_cases`.

`ground_truth_label` is fixture metadata for tests and demos; reconciliation
logic should not use it as an input signal. The timing-difference rows
intentionally have no bank row because settlement has not arrived yet.

The three named demo scenarios are listed in `demo_manifest.csv`:

| Scenario | Invoice | Expected action |
| --- | --- | --- |
| Easy win | `INV-DEMO-FEE-0001` | `AUTO_RESOLVE` |
| Investigation | `INV-DEMO-REFUND-0001` | `HUMAN_REVIEW` |
| Hard case | `INV-DEMO-AMBIG-0001` | `ESCALATE` |

`INV-DEMO-AMBIG-0001` is deliberately ambiguous: its bank shortfall is
explained equally by the recorded fee and the recorded refund. The separate
`INV-DEMO-UNKNOWN-0001` fixture has a shortfall with neither explanation and
is labeled `unknown_other`.
