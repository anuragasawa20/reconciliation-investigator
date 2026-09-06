"""Smoke test the real structured LLM backend.

This script intentionally prints outputs, not secrets. It is meant for a local
developer run after exporting the required provider environment variables.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--invoice-id",
        default="INV-1045",
        help="Exception invoice to investigate with the real LLM path.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env",
        help="Optional .env-style file to load before importing provider config.",
    )
    args = parser.parse_args()

    if args.env_file.exists():
        _load_env_file(args.env_file)
        print(f"Loaded env from {args.env_file}", flush=True)
    elif args.env_file != ROOT / ".env":
        print(f"Env file not found: {args.env_file}", flush=True)
        return 2

    from agents.challenger import challenge_hypotheses
    from agents.evidence_collector import collect_evidence
    from agents.llm import LLMNotConfigured, default_llm_client
    from agents.planner import plan_investigation
    from agents.validator import validate_hypotheses

    print("LLM environment", flush=True)
    env_summary = _environment_summary()
    print(json.dumps(env_summary, indent=2, sort_keys=True), flush=True)

    client = default_llm_client()
    if client is None:
        print(
            "\nLLM is disabled. Set RECONCILIATION_LLM_ENABLED=true in .env plus the "
            "provider API key before running this smoke test.",
            flush=True,
        )
        return 2

    if not env_summary["key_present"]:
        print(
            f"\n{env_summary['expected_key_env']} is missing or empty in "
            f"{args.env_file}. Add the key, save the file, then rerun this script.",
            flush=True,
        )
        return 2

    try:
        print(
            "\nDirect structured completion (timeout applies; this is a live API call)",
            flush=True,
        )
        direct = client.complete_json(
            task="backend_llm_smoke_test",
            system_prompt="Return only valid JSON matching the supplied schema.",
            user_payload={
                "instruction": "Confirm this backend LLM smoke test is live.",
                "expected_signal": "real_provider_call",
            },
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ok": {"type": "boolean"},
                    "signal": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["ok", "signal", "summary"],
            },
        )
        print(json.dumps(direct, indent=2, sort_keys=True), flush=True)

        print(f"\nLoading exception {args.invoice_id}", flush=True)
        exception = _load_exception(args.invoice_id)
        print("Planning investigation", flush=True)
        plan = plan_investigation(exception, llm_client=client)
        print("Collecting evidence", flush=True)
        evidence_bundles = collect_evidence(plan.live_hypotheses, args.invoice_id)
        facts = {
            hypothesis: _facts_for_bundle(bundle)
            for hypothesis, bundle in evidence_bundles.items()
            if hypothesis != "unknown_other"
        }
        requested = [
            "unknown" if hypothesis == "unknown_other" else hypothesis
            for hypothesis in plan.live_hypotheses
        ]
        print("Validating hypotheses", flush=True)
        validations = validate_hypotheses(facts, requested, llm_client=client)
        print("Challenging leading conclusion", flush=True)
        challenge = challenge_hypotheses(
            exception,
            [validation.to_dict() for validation in validations],
            llm_client=client,
        )

        print(
            json.dumps(
                {
                    "invoice_id": args.invoice_id,
                    "planner": {"live_hypotheses": plan.live_hypotheses},
                    "validator": [validation.to_dict() for validation in validations],
                    "challenger": {
                        "hypothesis": challenge.hypothesis,
                        "checks_performed": [
                            {
                                "name": check.name,
                                "passed": check.passed,
                                "detail": check.detail,
                            }
                            for check in challenge.checks_performed
                        ],
                        "contradictory_evidence": challenge.contradictory_evidence,
                        "unresolved_questions": challenge.unresolved_questions,
                        "challenge_notes": challenge.challenge_notes,
                        "adjusted_confidence": str(challenge.adjusted_confidence),
                        "force_human_review": challenge.force_human_review,
                        "final_explanation": challenge.final_explanation,
                    },
                },
                indent=2,
                sort_keys=True,
            ),
            flush=True,
        )
    except LLMNotConfigured as exc:
        print(f"\nLLM is not configured: {exc}")
        return 2
    except Exception as exc:
        print(f"\nLLM smoke test failed: {type(exc).__name__}: {exc}")
        return 1

    return 0


def _environment_summary() -> dict[str, Any]:
    provider = os.getenv("RECONCILIATION_LLM_PROVIDER", "openrouter").strip().lower()
    key_name = "OPENROUTER_API_KEY" if provider == "openrouter" else "OPENAI_API_KEY"
    return {
        "enabled": os.getenv("RECONCILIATION_LLM_ENABLED"),
        "provider": provider,
        "model": os.getenv("RECONCILIATION_LLM_MODEL")
        or os.getenv("OPENROUTER_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "openrouter/free",
        "expected_key_env": key_name,
        "key_present": bool(os.getenv(key_name)),
        "timeout_seconds": os.getenv("RECONCILIATION_LLM_TIMEOUT_SECONDS", "45"),
    }


def _load_env_file(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _load_exception(invoice_id: str):
    from core.exceptions import split_exceptions
    from core.loader import load_datasets
    from core.matcher import MatchConfig, reconcile
    from core.normalizer import normalize_datasets

    loaded = load_datasets(ROOT / "data")
    normalized = normalize_datasets(loaded)
    reconciled = reconcile(normalized, MatchConfig())
    exceptions = {
        record.invoice_id: record for record in split_exceptions(reconciled).exceptions
    }
    return exceptions[invoice_id]


def _facts_for_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for tool_name, result in bundle.items():
        fact_name = tool_name.removeprefix("get_")
        records = result.get("records", [])
        status = result.get("status", "NOT_FOUND")
        if not records:
            facts.append(
                {
                    "fact": fact_name,
                    "value": None,
                    "source": result.get("source", "unknown"),
                    "source_record_id": None,
                    "retrieval_status": status,
                }
            )
        for record in records:
            if not isinstance(record, dict):
                continue
            source = str(result.get("source", "unknown")).split("+")[0]
            record_id = record.get("transaction_id") or record.get("settlement_id")
            for key, value in record.items():
                if key in {
                    "invoice_id",
                    "transaction_id",
                    "settlement_id",
                    "currency",
                } or value in (None, ""):
                    continue
                facts.append(
                    {
                        "fact": key,
                        "value": str(value),
                        "source": source,
                        "source_record_id": record_id,
                        "retrieval_status": status,
                    }
                )
            facts.append(
                {
                    "fact": fact_name,
                    "value": json.dumps(record, sort_keys=True, default=str),
                    "source": source,
                    "source_record_id": record_id,
                    "retrieval_status": status,
                }
            )
    return facts


if __name__ == "__main__":
    raise SystemExit(main())
