"""Check entrance contract invariants without installing execution dependencies.

Behavioral backend tests live in test_memory_session.py.
Run with: python -m unittest discover -s tests/core/session -p 'test_*.py'
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from aworld.core.session import RunError, RunOptions, RunResult, RunStatus, RunStopReason


class ContractModelTests(unittest.TestCase):
    def test_invalid_deadlines_cannot_become_unbounded_or_immediately_expired_runs(self):
        for value in (True, False, 0, -1, float("nan"), float("inf"), float("-inf"), "10"):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                RunOptions(timeout_seconds=value)

    def test_nonterminal_and_contradictory_results_are_rejected(self):
        cases = (
            (RunStatus.PENDING, RunStopReason.END_TURN, None),
            (RunStatus.RUNNING, RunStopReason.END_TURN, None),
            (RunStatus.CANCELLING, RunStopReason.CANCELLED, None),
            (RunStatus.COMPLETED, RunStopReason.CANCELLED, None),
            (RunStatus.FAILED, RunStopReason.ERROR, None),
            (RunStatus.CANCELLED, RunStopReason.ERROR, RunError("provider", "offline")),
            (RunStatus.COMPLETED, RunStopReason.END_TURN, RunError("provider", "offline")),
        )
        for status, reason, error in cases:
            with self.subTest(status=status, reason=reason), self.assertRaises(ValueError):
                RunResult("run-1", "session-1", status, reason, error=error)

    def test_failed_and_cancelled_results_can_preserve_partial_output(self):
        result = RunResult(
            "run-1", "session-1", RunStatus.FAILED,
            RunStopReason.DEADLINE_EXCEEDED, output="partial answer",
            error=RunError("deadline_exceeded", "Execution budget exhausted"),
        )
        cancelled = RunResult(
            "run-2", "session-1", RunStatus.CANCELLED,
            RunStopReason.CANCELLED, output="partial answer",
        )
        self.assertEqual(result.output, cancelled.output)
        self.assertTrue(result.status.is_terminal)
        self.assertTrue(cancelled.status.is_terminal)

    def test_public_import_requires_only_stdlib_and_does_not_load_execution_layers(self):
        code = """
import sys
from pathlib import Path
from typing import get_type_hints
sys.path.insert(0, sys.argv[1])
import aworld.core.session as api
for name in api.__all__:
    exported = getattr(api, name)
    get_type_hints(exported)
from aworld.core.session.protocols import AgentExecutor, ContextPolicy, HistoryStore
for cls in (api.Context, AgentExecutor, ContextPolicy, api.Session, api.RunHandle, api.RunContext, HistoryStore):
    for value in vars(cls).values():
        if isinstance(value, property):
            get_type_hints(value.fget)
        elif callable(value) and getattr(value, '__module__', '').startswith('aworld.core.session'):
            get_type_hints(value)
for prefix in ('aworld.core.task', 'aworld.runners', 'aworld.sandbox',
               'aworld.core.context.amni', 'aworld_cli'):
    assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules), prefix
"""
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code, str(ROOT)],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
