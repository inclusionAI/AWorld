"""
/commit command - Smart git commit workflow

This is a Prompt Command (agent-mediated execution).
"""
import subprocess
from typing import Optional, List

from aworld_cli.core.command_system import Command, CommandContext, register_command


@register_command
class CommitCommand(Command):
    """
    Create a git commit with intelligent message generation.

    Type: Prompt Command (agent-mediated)
    Flow: Command → Agent → git_commit() → Result

    The agent analyzes git status, diff, and recent commits to:
    1. Understand the nature of changes
    2. Draft an appropriate commit message
    3. Stage files if needed
    4. Create the commit
    """

    @property
    def name(self) -> str:
        return "commit"

    @property
    def description(self) -> str:
        return "Create a git commit with intelligent message generation"

    @property
    def command_type(self) -> str:
        return "prompt"  # Agent-mediated execution

    @property
    def allowed_tools(self) -> List[str]:
        """Tools the agent can use for this command"""
        return [
            # Terminal git commands
            "terminal__mcp_execute_command",  # For git commands
            # Git tools
            "git_status",
            "git_diff",
            "git_log",
            "git_commit",
            # Filesystem tools (to check files if needed)
            "filesystem__read_file",
            "filesystem__list_directory",
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
            return "Not a git repository. Run 'git init' to create one."
        except subprocess.TimeoutExpired:
            return "Git command timed out"
        except FileNotFoundError:
            return "Git is not installed or not in PATH"
        except Exception as e:
            return f"Error checking git repository: {str(e)}"

    async def get_prompt(self, context: CommandContext) -> str:
        """Generate commit prompt with git context"""

        # Gather git context
        try:
            # Get status
            status_result = subprocess.run(
                ["git", "status", "--short"],
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=5
            )
            status = status_result.stdout.strip()

            # Get diff
            diff_result = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=5
            )
            diff = diff_result.stdout

            # Get recent commits for style reference
            log_result = subprocess.run(
                ["git", "log", "--oneline", "-10"],
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=5
            )
            recent_commits = log_result.stdout.strip()

            # Get current branch
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=5
            )
            branch = branch_result.stdout.strip()

        except Exception as e:
            # Fallback if context gathering fails
            status = "Unable to get git status"
            diff = "Unable to get git diff"
            recent_commits = "Unable to get recent commits"
            branch = "unknown"

        # Limit diff size
        if len(diff) > 10000:
            diff = diff[:10000] + "\n\n... (diff truncated, showing first 10KB)"

        return f"""## Git Commit Task

You are creating a git commit. Follow this process carefully.

### 1. Current Context

**Branch**: {branch}

**Status**:
```
{status if status else "(no changes)"}
```

**Changes (diff)**:
```diff
{diff if diff else "(no diff)"}
```

**Recent Commits** (for style reference):
```
{recent_commits if recent_commits else "(no commits)"}
```

### 2. Analysis

Analyze the changes and determine:
- Type of change: feat / fix / refactor / docs / test / chore
- Scope: What component/module is affected?
- Impact: What problem does this solve?

### 3. Commit Guidelines

CRITICAL RULES - NEVER VIOLATE:
- NEVER use `git commit --amend` (always create NEW commits)
- NEVER skip hooks (--no-verify, --no-gpg-sign)
- NEVER commit secrets (.env, credentials, API keys)
- NEVER use generic messages like "update" or "fix bug"

Message Format:
- First line: Concise summary (50-72 chars)
- Focus on WHY not WHAT (what is visible in diff)
- Follow this repository's commit style (see recent commits above)

Use HEREDOC format for commit message:
```bash
git commit -m "$(cat <<'EOF'
Your commit message here.

Optional extended description if needed.
EOF
)"
```

### 4. Execution Steps

1. **Stage files if needed**: Call git_status() to check, then use terminal:git add <files> if unstaged changes exist
2. **Create commit**: Use the HEREDOC format above with terminal:git commit OR call git_commit() tool with the message
3. **Report result**: Show the commit hash and summary

### 5. Execute Now

Begin the commit workflow. Use git_status, git_diff, git_log, and git_commit tools.
Do NOT explain your steps - just execute them efficiently.
"""
