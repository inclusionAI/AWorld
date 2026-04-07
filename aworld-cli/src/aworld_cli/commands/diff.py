"""
/diff command - Show and summarize git changes

This is a Prompt Command (agent-mediated execution).
"""
import subprocess
from typing import Optional, List

from aworld_cli.core.command_system import Command, CommandContext, register_command


@register_command
class DiffCommand(Command):
    """
    Show and summarize code changes.

    Type: Prompt Command (agent-mediated)
    Flow: Command → Agent → git_diff() → Summary

    Usage:
        /diff          Show changes vs HEAD
        /diff main     Show changes vs main branch
        /diff abc123   Show changes vs commit abc123

    The agent analyzes the diff and provides:
    - High-level overview
    - Key changes grouped by file
    - Impact assessment
    """

    @property
    def name(self) -> str:
        return "diff"

    @property
    def description(self) -> str:
        return "Show and summarize code changes"

    @property
    def command_type(self) -> str:
        return "prompt"  # Agent-mediated execution

    @property
    def allowed_tools(self) -> List[str]:
        """Tools the agent can use for this command"""
        return [
            "git_diff",
            "git_log",
            "git_status",
            # Optional: read files for more context
            "filesystem__read_file",
        ]

    async def pre_execute(self, context: CommandContext) -> Optional[str]:
        """Check if we're in a git repository"""
        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=context.cwd,
                capture_output=True,
                check=True,
                timeout=5
            )
            return None
        except subprocess.CalledProcessError:
            return "Not a git repository. This command requires git."
        except Exception as e:
            return f"Error: {str(e)}"

    async def get_prompt(self, context: CommandContext) -> str:
        """Generate diff summary prompt"""

        # Get ref from user args (default to HEAD)
        ref = context.user_args.strip() or "HEAD"

        # Gather diff context
        try:
            # Get diff
            diff_result = subprocess.run(
                ["git", "diff", ref],
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=10
            )
            diff = diff_result.stdout

            # Get changed files list
            files_result = subprocess.run(
                ["git", "diff", "--name-only", ref],
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=5
            )
            files = [f for f in files_result.stdout.strip().split("\n") if f]

            # Get file stats
            stat_result = subprocess.run(
                ["git", "diff", "--stat", ref],
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=5
            )
            stats = stat_result.stdout.strip()

        except Exception as e:
            diff = "Unable to get diff"
            files = []
            stats = "Unable to get stats"

        # Limit diff size
        if len(diff) > 30000:
            diff = diff[:30000] + "\n\n... (diff truncated, showing first 30KB)"

        files_list = "\n".join(f"- {f}" for f in files) if files else "(no files changed)"

        return f"""## Diff Summary Task

Compare current state with `{ref}`.

### Changed Files ({len(files)}):
{files_list}

### File Statistics:
```
{stats if stats else "(no stats)"}
```

### Full Diff:
```diff
{diff if diff else "(no changes)"}
```

## Task

Provide a structured summary of the changes:

### 1. Overview (1-2 sentences)
High-level description of what changed and why (infer from diff).

### 2. Key Changes
Bullet list of major modifications:
- What was added?
- What was removed?
- What was modified?

### 3. Files Affected (grouped by purpose)
Group files by their role and summarize changes:
- **Source Code**: Main logic changes
- **Tests**: Test modifications
- **Configuration**: Config/build changes
- **Documentation**: Docs updates
- **Other**: Misc changes

For each group, briefly describe what changed.

### 4. Impact Assessment
What are the implications of these changes?
- **Breaking Changes**: Any API/behavior changes that break compatibility?
- **New Features**: What new capabilities are added?
- **Bug Fixes**: What bugs are fixed?
- **Deprecations**: Anything marked as deprecated?
- **Dependencies**: Any dependency changes?

### 5. Review Recommendations
Quick assessment:
- Are changes focused and coherent?
- Is scope appropriate (not too large)?
- Any obvious issues to address before merge?

## Instructions

- Be concise but comprehensive
- Focus on semantic changes, not syntax
- Use git_diff and git_log tools if needed
- Highlight breaking changes prominently
- If diff is too large, summarize the most important parts

Begin the diff analysis now.
"""
