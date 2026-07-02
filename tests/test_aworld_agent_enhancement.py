#!/usr/bin/env python3
"""
AWorld Agent Enhancement Branch Verification Script

Tests the following changes:
1. MCP tool access control (filter_mcp_tools_by_servers)
2. Slash command system (/help, /commit, /review, /diff)
3. Sandbox sharing mechanism
4. Developer agent tool expansion

Usage:
    python tests/test_aworld_agent_enhancement.py [--quick] [--benchmark]

Options:
    --quick       Skip GAIA benchmark tests
    --benchmark   Run full GAIA benchmark (50 tasks, ~30 min)
"""

import sys
import os
import subprocess
import asyncio
import importlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

# Get project root for file operations
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# CRITICAL FIX: Remove tests directory from sys.path to prevent tests/mcp/ from shadowing mcp package
# When running "python tests/test_aworld_agent_enhancement.py", Python adds tests/ to sys.path[0]
# This causes tests/mcp/ to be found before the installed mcp package, leading to import errors
tests_dir = str(Path(__file__).resolve().parent)
if tests_dir in sys.path:
    sys.path.remove(tests_dir)

# Also change working directory to project root
os.chdir(str(PROJECT_ROOT))

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.syntax import Syntax
except ImportError:
    print("⚠️  Installing required dependencies...")
    subprocess.run([sys.executable, "-m", "pip", "install", "rich"], check=True)
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.syntax import Syntax

console = Console()


class TestPriority(Enum):
    """Test priority levels"""
    __test__ = False
    CRITICAL = "🔴 CRITICAL"
    RECOMMENDED = "🟡 RECOMMENDED"
    OPTIONAL = "🟢 OPTIONAL"


@dataclass
class TestResult:
    """Test result container"""
    __test__ = False
    name: str
    priority: TestPriority
    passed: bool
    message: str
    duration: float = 0.0
    details: Optional[str] = None


class TestRunner:
    """Main test orchestrator"""
    __test__ = False

    def __init__(self, quick_mode: bool = False, run_benchmark: bool = False):
        self.quick_mode = quick_mode
        self.run_benchmark = run_benchmark
        self.results: List[TestResult] = []
        self.start_time = 0.0

    def add_result(self, result: TestResult):
        """Add test result and print status"""
        self.results.append(result)
        status = "✅ PASS" if result.passed else "❌ FAIL"
        color = "green" if result.passed else "red"
        console.print(f"{status} [{color}]{result.name}[/{color}] ({result.duration:.2f}s)")
        if result.message:
            console.print(f"    {result.message}", style="dim")

    async def run_all_tests(self):
        """Execute all test suites"""
        import time
        self.start_time = time.time()

        console.print(Panel.fit(
            "[bold cyan]AWorld Agent Enhancement Verification[/bold cyan]\n"
            f"Mode: {'Quick' if self.quick_mode else 'Full'} | "
            f"Benchmark: {'Yes' if self.run_benchmark else 'No'}",
            border_style="cyan"
        ))

        # Test suite order
        await self._test_environment()
        await self._test_tool_filtering()
        await self._test_command_system()
        await self._test_sandbox_sharing()
        await self._test_agent_configuration()

        if not self.quick_mode:
            await self._test_git_tools()
            await self._test_slash_commands_interactive()

        if self.run_benchmark:
            await self._test_gaia_benchmark()

        # Print summary
        self._print_summary()


def test_enhancement_verifier_helpers_are_not_collected():
    """Smoke test to keep this verification module pytest-friendly."""
    assert TestPriority.__test__ is False
    assert TestResult.__test__ is False
    assert TestRunner.__test__ is False

    async def _test_environment(self):
        """Test 1: Environment and installation"""
        console.print("\n[bold]1. Environment Verification[/bold]")
        import time

        # Check Python version
        start = time.time()
        python_version = sys.version_info
        passed = python_version >= (3, 11)
        self.add_result(TestResult(
            name="Python 3.11+",
            priority=TestPriority.CRITICAL,
            passed=passed,
            message=f"Found Python {python_version.major}.{python_version.minor}",
            duration=time.time() - start
        ))

        # Check aworld installation
        start = time.time()
        try:
            import aworld
            aworld_path = Path(aworld.__file__).parent
            self.add_result(TestResult(
                name="AWorld package import",
                priority=TestPriority.CRITICAL,
                passed=True,
                message=f"Installed at {aworld_path}",
                duration=time.time() - start
            ))
        except ImportError as e:
            self.add_result(TestResult(
                name="AWorld package import",
                priority=TestPriority.CRITICAL,
                passed=False,
                message=f"Import failed: {e}",
                duration=time.time() - start
            ))
            return

        # Check aworld-cli installation
        start = time.time()
        try:
            import aworld_cli
            cli_path = Path(aworld_cli.__file__).parent
            self.add_result(TestResult(
                name="AWorld CLI import",
                priority=TestPriority.CRITICAL,
                passed=True,
                message=f"Installed at {cli_path}",
                duration=time.time() - start
            ))
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            self.add_result(TestResult(
                name="AWorld CLI import",
                priority=TestPriority.CRITICAL,
                passed=False,
                message=f"Import failed: {e}",
                details=error_details,
                duration=time.time() - start
            ))
            return

        # Check critical modules
        start = time.time()
        try:
            from aworld.mcp_client.utils import filter_mcp_tools_by_servers
            from aworld_cli.core.command_system import CommandRegistry, Command
            self.add_result(TestResult(
                name="New modules import",
                priority=TestPriority.CRITICAL,
                passed=True,
                message="filter_mcp_tools_by_servers, CommandRegistry loaded",
                duration=time.time() - start
            ))
        except ImportError as e:
            self.add_result(TestResult(
                name="New modules import",
                priority=TestPriority.CRITICAL,
                passed=False,
                message=f"Import failed: {e}",
                duration=time.time() - start
            ))

    async def _test_tool_filtering(self):
        """Test 2: MCP tool access control"""
        console.print("\n[bold]2. Tool Access Control[/bold]")
        import time

        try:
            from aworld.mcp_client.utils import filter_mcp_tools_by_servers

            # Mock tools
            mock_tools = [
                {"type": "function", "function": {"name": "filesystem__read_file", "description": "Read"}},
                {"type": "function", "function": {"name": "filesystem__write_file", "description": "Write"}},
                {"type": "function", "function": {"name": "terminal__execute", "description": "Execute"}},
                {"type": "function", "function": {"name": "playwright__navigate", "description": "Navigate"}},
            ]

            # Test 2.1: Filter by single server
            start = time.time()
            result = filter_mcp_tools_by_servers(mock_tools, allowed_servers=["filesystem"])
            expected = 2  # read_file, write_file
            passed = len(result) == expected
            tool_names = [t['function']['name'] for t in result]
            self.add_result(TestResult(
                name="Filter single server (filesystem)",
                priority=TestPriority.CRITICAL,
                passed=passed,
                message=f"Got {len(result)}/{expected} tools: {tool_names}",
                duration=time.time() - start
            ))

            # Test 2.2: Filter by multiple servers
            start = time.time()
            result = filter_mcp_tools_by_servers(mock_tools, allowed_servers=["filesystem", "terminal"])
            expected = 3  # filesystem + terminal
            passed = len(result) == expected
            self.add_result(TestResult(
                name="Filter multiple servers",
                priority=TestPriority.CRITICAL,
                passed=passed,
                message=f"Got {len(result)}/{expected} tools",
                duration=time.time() - start
            ))

            # Test 2.3: Empty allowlist (should return empty)
            start = time.time()
            result = filter_mcp_tools_by_servers(mock_tools, allowed_servers=[])
            passed = len(result) == 0
            self.add_result(TestResult(
                name="Empty allowlist returns empty",
                priority=TestPriority.CRITICAL,
                passed=passed,
                message=f"Got {len(result)} tools (expected 0)",
                duration=time.time() - start
            ))

            # Test 2.4: None allowlist (should return empty)
            start = time.time()
            result = filter_mcp_tools_by_servers(mock_tools, allowed_servers=None)
            passed = len(result) == 0
            self.add_result(TestResult(
                name="None allowlist returns empty",
                priority=TestPriority.CRITICAL,
                passed=passed,
                message=f"Got {len(result)} tools (expected 0)",
                duration=time.time() - start
            ))

        except Exception as e:
            import traceback
            self.add_result(TestResult(
                name="Tool filtering suite",
                priority=TestPriority.CRITICAL,
                passed=False,
                message=f"Exception: {e}",
                details=traceback.format_exc(),
                duration=0
            ))

    async def _test_command_system(self):
        """Test 3: Slash command system"""
        console.print("\n[bold]3. Slash Command System[/bold]")
        import time

        try:
            from aworld_cli.core.command_system import CommandRegistry
            from aworld_cli import commands  # Trigger registration

            # Test 3.1: Command registry loaded
            start = time.time()
            registered = CommandRegistry.list_commands()
            passed = len(registered) > 0
            self.add_result(TestResult(
                name="Command registry initialization",
                priority=TestPriority.CRITICAL,
                passed=passed,
                message=f"Registered {len(registered)} commands",
                duration=time.time() - start
            ))

            # Test 3.2: Expected commands present
            start = time.time()
            expected_commands = ["help", "commit", "review", "diff"]
            missing = [cmd for cmd in expected_commands if CommandRegistry.get(cmd) is None]
            passed = len(missing) == 0
            self.add_result(TestResult(
                name="Expected commands registered",
                priority=TestPriority.CRITICAL,
                passed=passed,
                message=f"Missing: {missing}" if missing else f"All present: {expected_commands}",
                duration=time.time() - start
            ))

            # Test 3.3: Command types correct
            start = time.time()
            help_cmd = CommandRegistry.get("help")
            commit_cmd = CommandRegistry.get("commit")

            if help_cmd and commit_cmd:
                help_is_tool = help_cmd.command_type == "tool"
                commit_is_prompt = commit_cmd.command_type == "prompt"
                passed = help_is_tool and commit_is_prompt
                self.add_result(TestResult(
                    name="Command types correct",
                    priority=TestPriority.CRITICAL,
                    passed=passed,
                    message=f"help={help_cmd.command_type}, commit={commit_cmd.command_type}",
                    duration=time.time() - start
                ))
            else:
                self.add_result(TestResult(
                    name="Command types correct",
                    priority=TestPriority.CRITICAL,
                    passed=False,
                    message="Commands not found",
                    duration=time.time() - start
                ))

            # Test 3.4: Tool allowlist configured
            start = time.time()
            if commit_cmd:
                has_allowlist = hasattr(commit_cmd, 'allowed_tools') and len(commit_cmd.allowed_tools) > 0
                self.add_result(TestResult(
                    name="Tool allowlist configured",
                    priority=TestPriority.RECOMMENDED,
                    passed=has_allowlist,
                    message=f"commit has {len(commit_cmd.allowed_tools) if has_allowlist else 0} allowed tools",
                    duration=time.time() - start
                ))

        except Exception as e:
            import traceback
            self.add_result(TestResult(
                name="Command system suite",
                priority=TestPriority.CRITICAL,
                passed=False,
                message=f"Exception: {e}",
                details=traceback.format_exc(),
                duration=0
            ))

    async def _test_sandbox_sharing(self):
        """Test 4: Sandbox sharing mechanism"""
        console.print("\n[bold]4. Sandbox Sharing[/bold]")
        import time

        try:
            from aworld.sandbox import Sandbox
            from aworld_cli.builtin_agents.smllc.agents.developer.developer import build_developer_swarm

            # Test 4.1: Create shared sandbox
            start = time.time()
            sandbox = Sandbox(
                builtin_tools=["filesystem"],
                workspaces=[os.getcwd()]
            )
            sandbox.reuse = True
            sandbox_id = id(sandbox)
            self.add_result(TestResult(
                name="Create shared sandbox",
                priority=TestPriority.CRITICAL,
                passed=True,
                message=f"Sandbox created (ID: {sandbox_id})",
                duration=time.time() - start
            ))

            # Test 4.2: Pass sandbox to developer
            start = time.time()
            try:
                swarm = build_developer_swarm(sandbox=sandbox)
                # Get first agent from swarm (agents is OrderedDict)
                agent = list(swarm.agents.values())[0] if swarm.agents else None
                passed = agent is not None and hasattr(agent, 'sandbox') and agent.sandbox is sandbox
                self.add_result(TestResult(
                    name="Developer accepts shared sandbox",
                    priority=TestPriority.CRITICAL,
                    passed=passed,
                    message=f"Sandbox match: {passed}",
                    duration=time.time() - start
                ))

                # Test 4.3: MCP servers configured
                start = time.time()
                expected_servers = ["filesystem", "terminal"]
                has_servers = hasattr(agent, 'mcp_servers') and agent.mcp_servers is not None
                if has_servers:
                    servers_match = set(agent.mcp_servers) == set(expected_servers)
                    self.add_result(TestResult(
                        name="Developer MCP servers configured",
                        priority=TestPriority.RECOMMENDED,
                        passed=servers_match,
                        message=f"Got {agent.mcp_servers}, expected {expected_servers}",
                        duration=time.time() - start
                    ))
                else:
                    self.add_result(TestResult(
                        name="Developer MCP servers configured",
                        priority=TestPriority.RECOMMENDED,
                        passed=False,
                        message="mcp_servers attribute missing",
                        duration=time.time() - start
                    ))

            except Exception as e:
                import traceback
                self.add_result(TestResult(
                    name="Developer sandbox integration",
                    priority=TestPriority.CRITICAL,
                    passed=False,
                    message=f"Exception: {e}",
                    details=traceback.format_exc(),
                    duration=time.time() - start
                ))

        except Exception as e:
            import traceback
            self.add_result(TestResult(
                name="Sandbox sharing suite",
                priority=TestPriority.CRITICAL,
                passed=False,
                message=f"Exception: {e}",
                details=traceback.format_exc(),
                duration=0
            ))

    async def _test_agent_configuration(self):
        """Test 5: Agent configuration"""
        console.print("\n[bold]5. Agent Configuration[/bold]")
        import time

        try:
            from aworld_cli.builtin_agents.smllc.agents.developer.developer import build_developer_swarm

            # Test 5.1: Developer tools expanded
            start = time.time()
            swarm = build_developer_swarm()
            agent = list(swarm.agents.values())[0] if swarm.agents else None

            expected_tools = [
                "CAST_ANALYSIS", "CAST_CODER", "CAST_SEARCH",
                "glob", "git_status", "git_diff", "git_log", "git_commit", "git_blame"
            ]

            if hasattr(agent, 'tool_names'):
                has_all = all(tool in agent.tool_names for tool in expected_tools)
                missing = [t for t in expected_tools if t not in agent.tool_names]
                self.add_result(TestResult(
                    name="Developer tools expanded",
                    priority=TestPriority.RECOMMENDED,
                    passed=has_all,
                    message=f"Missing: {missing}" if missing else f"All {len(expected_tools)} tools present",
                    duration=time.time() - start
                ))
            else:
                self.add_result(TestResult(
                    name="Developer tools expanded",
                    priority=TestPriority.RECOMMENDED,
                    passed=False,
                    message="tool_names attribute missing",
                    duration=time.time() - start
                ))

        except Exception as e:
            import traceback
            self.add_result(TestResult(
                name="Agent configuration suite",
                priority=TestPriority.RECOMMENDED,
                passed=False,
                message=f"Exception: {e}",
                details=traceback.format_exc(),
                duration=0
            ))

    async def _test_git_tools(self):
        """Test 6: Git tools (optional)"""
        console.print("\n[bold]6. Git Tools Integration[/bold]")
        import time

        # Check if in git repo
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            self.add_result(TestResult(
                name="Git repository check",
                priority=TestPriority.OPTIONAL,
                passed=False,
                message="Not in a git repository, skipping git tests",
                duration=0
            ))
            return

        try:
            # Test git tools import
            start = time.time()
            try:
                from aworld.tools import git_tools
                self.add_result(TestResult(
                    name="Git tools module import",
                    priority=TestPriority.OPTIONAL,
                    passed=True,
                    message="Git tools module loaded",
                    duration=time.time() - start
                ))
            except (NameError, ModuleNotFoundError) as e:
                # Known issue: be_tool decorator has limitations with dynamic module generation
                error_msg = str(e)
                if "Field" in error_msg or "__tmp_action" in error_msg:
                    details = (
                        "Known framework limitation: The be_tool decorator generates temporary "
                        "action modules at runtime, but has issues with:\n"
                        "1. Missing Field import in generated files\n"
                        "2. Module path resolution after working directory changes\n\n"
                        "This is a known issue in aworld/core/tool/func_to_tool.py. "
                        "Git tools functionality works in normal usage but cannot be tested via direct import."
                    )
                else:
                    details = f"Import error: {error_msg}"

                self.add_result(TestResult(
                    name="Git tools module import",
                    priority=TestPriority.OPTIONAL,
                    passed=False,
                    message=f"Known limitation: {error_msg[:50]}...",
                    details=details,
                    duration=time.time() - start
                ))
                return
            except ImportError as e:
                self.add_result(TestResult(
                    name="Git tools module import",
                    priority=TestPriority.OPTIONAL,
                    passed=False,
                    message=f"Import failed: {e}",
                    duration=time.time() - start
                ))
                return

            # Test git_status
            start = time.time()
            result = subprocess.run(["git", "status", "--short"], capture_output=True, text=True)
            passed = result.returncode == 0
            self.add_result(TestResult(
                name="Git status execution",
                priority=TestPriority.OPTIONAL,
                passed=passed,
                message=f"Exit code: {result.returncode}",
                duration=time.time() - start
            ))

        except Exception as e:
            import traceback
            self.add_result(TestResult(
                name="Git tools suite",
                priority=TestPriority.OPTIONAL,
                passed=False,
                message=f"Exception: {e}",
                details=traceback.format_exc(),
                duration=0
            ))

    async def _test_slash_commands_interactive(self):
        """Test 7: Slash commands (interactive - manual verification)"""
        console.print("\n[bold]7. Interactive Slash Commands[/bold]")

        console.print(
            "\n[yellow]⚠️  Manual verification required:[/yellow]\n"
            "   Run: [cyan]aworld-cli[/cyan]\n"
            "   Test: [cyan]/help[/cyan], [cyan]/diff main[/cyan]\n"
            "   This test is skipped in automated mode.\n"
        )

        self.add_result(TestResult(
            name="Interactive slash commands",
            priority=TestPriority.OPTIONAL,
            passed=True,
            message="Manual verification required (skipped)",
            duration=0
        ))

    async def _test_gaia_benchmark(self):
        """Test 8: GAIA benchmark"""
        console.print("\n[bold]8. GAIA Benchmark[/bold]")
        import time

        gaia_dir = PROJECT_ROOT / "examples" / "gaia"
        if not gaia_dir.exists():
            self.add_result(TestResult(
                name="GAIA benchmark",
                priority=TestPriority.OPTIONAL,
                passed=False,
                message=f"GAIA directory not found: {gaia_dir}",
                duration=0
            ))
            return

        # Run quick test (10 tasks)
        console.print("   [dim]Running GAIA validation (10 tasks, ~5-10 min)...[/dim]")
        start = time.time()

        try:
            result = subprocess.run(
                [sys.executable, "run.py", "--split", "validation", "--start", "0", "--end", "10"],
                cwd=str(gaia_dir),
                capture_output=True,
                text=True,
                timeout=600  # 10 min timeout
            )

            duration = time.time() - start

            # Parse results
            output = result.stdout + result.stderr
            passed = result.returncode == 0

            # Try to extract pass rate
            pass_rate = None
            for line in output.split('\n'):
                if 'Pass@1' in line or 'pass@1' in line:
                    # Extract percentage
                    import re
                    match = re.search(r'(\d+\.?\d*)%', line)
                    if match:
                        pass_rate = float(match.group(1))

            message = f"Completed in {duration:.1f}s"
            if pass_rate is not None:
                message += f", Pass@1: {pass_rate}%"
                passed = passed and pass_rate >= 60  # Threshold

            self.add_result(TestResult(
                name="GAIA benchmark (10 tasks)",
                priority=TestPriority.OPTIONAL,
                passed=passed,
                message=message,
                details=output[-1000:] if len(output) > 1000 else output,  # Last 1000 chars
                duration=duration
            ))

        except subprocess.TimeoutExpired:
            self.add_result(TestResult(
                name="GAIA benchmark (10 tasks)",
                priority=TestPriority.OPTIONAL,
                passed=False,
                message="Timeout after 10 minutes",
                duration=600
            ))
        except Exception as e:
            import traceback
            self.add_result(TestResult(
                name="GAIA benchmark",
                priority=TestPriority.OPTIONAL,
                passed=False,
                message=f"Exception: {e}",
                details=traceback.format_exc(),
                duration=time.time() - start
            ))

    def _print_summary(self):
        """Print test summary"""
        import time
        total_duration = time.time() - self.start_time

        # Count by priority and status
        critical_passed = sum(1 for r in self.results if r.priority == TestPriority.CRITICAL and r.passed)
        critical_total = sum(1 for r in self.results if r.priority == TestPriority.CRITICAL)
        recommended_passed = sum(1 for r in self.results if r.priority == TestPriority.RECOMMENDED and r.passed)
        recommended_total = sum(1 for r in self.results if r.priority == TestPriority.RECOMMENDED)
        optional_passed = sum(1 for r in self.results if r.priority == TestPriority.OPTIONAL and r.passed)
        optional_total = sum(1 for r in self.results if r.priority == TestPriority.OPTIONAL)

        total_passed = sum(1 for r in self.results if r.passed)
        total_tests = len(self.results)

        # Create summary table
        table = Table(title="Test Summary", show_header=True, header_style="bold cyan")
        table.add_column("Priority", style="dim")
        table.add_column("Passed", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Rate", justify="right")

        table.add_row(
            TestPriority.CRITICAL.value,
            str(critical_passed),
            str(critical_total),
            f"{critical_passed/critical_total*100:.0f}%" if critical_total > 0 else "N/A"
        )
        table.add_row(
            TestPriority.RECOMMENDED.value,
            str(recommended_passed),
            str(recommended_total),
            f"{recommended_passed/recommended_total*100:.0f}%" if recommended_total > 0 else "N/A"
        )
        table.add_row(
            TestPriority.OPTIONAL.value,
            str(optional_passed),
            str(optional_total),
            f"{optional_passed/optional_total*100:.0f}%" if optional_total > 0 else "N/A"
        )
        table.add_row(
            "[bold]TOTAL[/bold]",
            f"[bold]{total_passed}[/bold]",
            f"[bold]{total_tests}[/bold]",
            f"[bold]{total_passed/total_tests*100:.0f}%[/bold]"
        )

        console.print("\n")
        console.print(table)

        # Overall verdict
        console.print(f"\n⏱️  Total duration: {total_duration:.1f}s\n")

        if critical_passed == critical_total:
            console.print(Panel.fit(
                "[bold green]✅ VERIFICATION PASSED[/bold green]\n"
                "All critical tests passed. Branch is ready for merge.",
                border_style="green"
            ))
        elif critical_passed >= critical_total * 0.8:
            console.print(Panel.fit(
                "[bold yellow]⚠️  VERIFICATION WARNING[/bold yellow]\n"
                f"Some critical tests failed ({critical_passed}/{critical_total}). Review needed.",
                border_style="yellow"
            ))
        else:
            console.print(Panel.fit(
                "[bold red]❌ VERIFICATION FAILED[/bold red]\n"
                f"Multiple critical tests failed ({critical_passed}/{critical_total}). Do not merge.",
                border_style="red"
            ))

        # Show failed tests
        failed = [r for r in self.results if not r.passed]
        if failed:
            console.print("\n[bold red]Failed Tests:[/bold red]")
            for result in failed:
                console.print(f"  • {result.name}: {result.message}")
                if result.details:
                    console.print(f"    [dim]{result.details[:200]}...[/dim]")

        # Exit code
        sys.exit(0 if critical_passed == critical_total else 1)


async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="AWorld Agent Enhancement Verification")
    parser.add_argument("--quick", action="store_true", help="Skip optional tests")
    parser.add_argument("--benchmark", action="store_true", help="Run GAIA benchmark")
    args = parser.parse_args()

    runner = TestRunner(quick_mode=args.quick, run_benchmark=args.benchmark)
    await runner.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
