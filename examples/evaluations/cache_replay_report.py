"""Build a redacted deterministic cache-replay report from captured calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aworld.evaluations.cache_replay import evaluate_cache_replay  # noqa: E402


def _load_calls(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(
        isinstance(call, dict) for call in payload
    ):
        raise ValueError(f"provider calls must be a JSON array: {path}")
    return payload


def build_portfolio(paths: list[Path]) -> dict[str, Any]:
    if not paths:
        raise ValueError("at least one provider-calls path is required")
    runs = []
    total_calls = 0
    trace_matches = 0
    exact_usages = 0
    break_events = 0
    explained_breaks = 0
    unexplained_breaks = 0
    for path in paths:
        report = evaluate_cache_replay(_load_calls(path))
        payload = report.to_dict()
        call_count = len(report.calls)
        total_calls += call_count
        trace_matches += sum(call.trace_match for call in report.calls)
        exact_usages += sum(call.exact_usage for call in report.calls)
        break_events += report.break_event_count
        explained_breaks += report.explained_break_count
        unexplained_breaks += report.unexplained_break_count
        runs.append(
            {
                "source_hash": _source_hash(path),
                "call_count": call_count,
                "report": payload,
            }
        )
    passed = all(run["report"]["status"] == "passed" for run in runs)
    return {
        "schema_version": "aworld.context.cache-replay-portfolio.v1",
        "status": "passed" if passed else "failed",
        "run_count": len(runs),
        "call_count": total_calls,
        "request_trace_match_rate": (
            trace_matches / total_calls if total_calls else 0.0
        ),
        "exact_usage_coverage": (
            exact_usages / total_calls if total_calls else 0.0
        ),
        "break_event_count": break_events,
        "explained_break_count": explained_breaks,
        "unexplained_break_count": unexplained_breaks,
        "runs": runs,
    }


def _source_hash(path: Path) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider_calls", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_portfolio(args.provider_calls)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
