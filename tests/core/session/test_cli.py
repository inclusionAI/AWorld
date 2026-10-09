"""Run the new source CLI in isolated stdlib subprocesses and real stdin pipes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from aworld._version import __version__

ROOT = Path(__file__).resolve().parents[3]


def command(*args, input=None):
    env = dict(os.environ)
    for key in ("AWORLD_MODEL", "OPENAI_MODEL", "LLM_MODEL_NAME", "AWORLD_API_KEY",
                "OPENAI_API_KEY", "LLM_API_KEY", "AWORLD_AUTO_DOTENV"):
        env.pop(key, None)
    env["AWORLD_DISABLE_AUTO_DOTENV"] = "1"
    return subprocess.run([sys.executable, "-I", "-S", "-c",
        "import sys; sys.path.insert(0, sys.argv.pop(1)); from aworld.cli.main import main; raise SystemExit(main())",
        str(ROOT), *args, "--no-skills"], input=input, capture_output=True, text=True, env=env, timeout=10)


class CliTests(unittest.TestCase):
    def test_help_version_and_capabilities_without_optional_dependencies(self):
        result = command("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Agent + Context + Tool", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertIn(__version__, command("--version").stdout)
        tools = json.loads(command("tools", "--json").stdout)
        self.assertEqual([tool["name"] for tool in tools], ["read", "write", "bash", "read_session", "search_sessions", "session_query"])

    def test_follow_up_runs_share_context_and_events_are_observations(self):
        result = command("run", "--demo", "--task", "first", "--follow-up", "second", "--json", "--events")
        self.assertEqual(result.returncode, 0, result.stderr)
        runs = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([run["output"] for run in runs], ["demo: first", "demo: second"])
        self.assertEqual(runs[0]["session_id"], runs[1]["session_id"])
        events = [json.loads(line) for line in result.stderr.splitlines()]
        self.assertEqual(sum(event["type"] == "run.finished" for event in events), 2)

    def test_demo_calls_real_read_tool_in_declared_local_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "note").write_text("local data\n")
            result = command("run", "--demo", "--cwd", directory, "--task", "read note", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("local data", json.loads(result.stdout)["output"])

    def test_interactive_pipe_multiple_sessions_and_query(self):
        result = command("chat", "--demo", "--json", input='first\n/new\nsecond\n/sessions\n/query {"text":"first"}\n/quit\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertNotEqual(lines[0]["session_id"], lines[1]["session_id"])
        self.assertEqual(len(lines[2]), 2)
        self.assertEqual(lines[3]["matches"][0]["session_id"], lines[0]["session_id"])

    def test_trajectory_contains_real_calls_results_and_follow_up_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note").write_text("observed data")
            result = command("run", "--demo", "--cwd", directory, "--task", "read note",
                "--follow-up", "second", "--trajectory-output", str(root / "trajectory.json"),
                "--result-output", str(root / "result.json"))
            self.assertEqual(result.returncode, 0, result.stderr)
            trajectory = json.loads((root / "trajectory.json").read_text())
            outcome = json.loads((root / "result.json").read_text())
            self.assertEqual(trajectory["session_id"], outcome["session_id"])
            self.assertEqual([step["step_id"] for step in trajectory["steps"]], list(range(1, len(trajectory["steps"]) + 1)))
            step = next(step for step in trajectory["steps"] if step.get("tool_calls"))
            self.assertEqual(step["tool_calls"][0]["function_name"], "read")
            observation = step["observation"]["results"][0]
            self.assertEqual(observation["source_call_id"], step["tool_calls"][0]["tool_call_id"])
            self.assertIn("observed data", observation["content"])
            self.assertEqual(trajectory["extra"]["status"], "completed")
            self.assertEqual(trajectory["final_metrics"]["extra"]["llm_request_count"], 3)
            self.assertEqual(trajectory["final_metrics"]["extra"]["tool_call_count"], 1)
            self.assertEqual(trajectory["extra"]["run_metrics"]["llm_request_count"], 1)
            self.assertEqual(trajectory["extra"]["run_metrics"]["tool_call_count"], 0)
            self.assertEqual(step["tool_calls"][0]["extra"]["timing_source"], "aworld.run_events")
            self.assertGreaterEqual(step["tool_calls"][0]["extra"]["duration_ms"], 0)

    def test_explicit_tool_selection_and_configuration_errors(self):
        self.assertEqual([tool["name"] for tool in json.loads(command("tools", "--tools", "read", "--json").stdout)], ["read"])
        self.assertEqual(json.loads(command("tools", "--no-tools", "--json").stdout), [])
        result = command("run", "--task", "hello")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Specify --model", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(command("run", "--demo", "--task", "hello", "--max-turns", "0").returncode, 2)

    def test_summary_budget_defaults_and_overrides_in_exported_trajectory(self):
        cases = [
            (128000, 32768, None, 32768),
            (1000000, 32768, None, 32768),
            (65536, 32768, None, 19045),
            (128000, 4096, None, 4096),
            (128000, 32768, 8192, 8192),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.json"
            for window, output, override, expected in cases:
                with self.subTest(window=window, output=output, override=override):
                    arguments = ["run", "--demo", "--task", "hello", "--context-window", str(window),
                                 "--max-output-tokens", str(output), "--trajectory-output", str(path)]
                    if override is not None:
                        arguments.extend(["--summary-max-tokens", str(override)])
                    result = command(*arguments)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    budget = json.loads(path.read_text())["extra"]["context_budget"]["budget"]
                    self.assertEqual(budget["summary_max_tokens"], expected)
                    self.assertEqual(budget["summary_timeout"], 300)
                    self.assertLess(budget["keep_recent_tokens"] + expected,
                                    int((window - output - budget["safety_margin"]) * budget["trigger_ratio"]))

    def test_summary_timeout_override_is_exported_and_invalid_values_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.json"
            result = command("run", "--demo", "--task", "hello", "--compaction-timeout", "145.5",
                             "--trajectory-output", str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(path.read_text())["extra"]["context_budget"]["budget"]["summary_timeout"], 145.5)
            for value in ("0", "-1", "nan", "inf"):
                with self.subTest(value=value):
                    result = command("run", "--demo", "--task", "hello", "--compaction-timeout", value)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("summary_timeout must be positive and finite", result.stderr)

    def test_live_request_timeout_defaults_to_five_minutes(self):
        from aworld.cli.main import parser

        self.assertEqual(parser().parse_args([]).request_timeout, 300)


if __name__ == "__main__":
    unittest.main()
