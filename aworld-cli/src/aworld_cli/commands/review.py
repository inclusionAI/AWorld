"""
/review command - Code review assistant

This is a Prompt Command (agent-mediated execution).
"""
import subprocess
from typing import Optional, List

from aworld_cli.core.command_system import Command, CommandContext, register_command


@register_command
class ReviewCommand(Command):
    """
    Perform comprehensive code review on current changes.

    Type: Prompt Command (agent-mediated)
    Flow: Command → Agent → Analysis → Result

    The agent reviews code for:
    - Code quality (bugs, best practices, error handling)
    - Design & architecture (soundness, simplicity, tech debt)
    - Testing (coverage, edge cases)
    - Security (vulnerabilities, input validation, secrets)
    - Performance (obvious issues, unnecessary operations)
    """

    @property
    def name(self) -> str:
        return "review"

    @property
    def description(self) -> str:
        return "Perform code review on current changes"

    @property
    def command_type(self) -> str:
        return "prompt"  # Agent-mediated execution

    @property
    def allowed_tools(self) -> List[str]:
        """Tools the agent can use for this command"""
        return [
            # Git tools for context
            "git_diff",
            "git_log",
            "git_status",
            # Filesystem tools for reading full file context
            "filesystem__read_file",
            "filesystem__list_directory",
            "filesystem__search_files",
            # CAST tools for deep code analysis
            "CAST_ANALYSIS",
            "CAST_SEARCH",
            # Glob for finding related files
            "glob",
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
        """Generate review prompt with git context"""

        # Gather changed files and diff
        try:
            # Get changed files
            files_result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=5
            )
            changed_files = [f for f in files_result.stdout.strip().split("\n") if f]

            # Get full diff
            diff_result = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=10
            )
            diff = diff_result.stdout

            # Get commit context if any
            log_result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=5
            )
            recent_commits = log_result.stdout.strip()

        except Exception as e:
            changed_files = []
            diff = "Unable to get diff"
            recent_commits = ""

        # Limit diff size
        if len(diff) > 20000:
            diff = diff[:20000] + "\n\n... (diff truncated, showing first 20KB)"

        files_list = "\n".join(f"- {f}" for f in changed_files) if changed_files else "(no files changed)"

        return f"""## Code Review Task

Review the following changes and provide detailed, actionable feedback.

### Changed Files ({len(changed_files)}):
{files_list}

### Full Diff:
```diff
{diff if diff else "(no changes)"}
```

### Recent Commits (context):
```
{recent_commits if recent_commits else "(no commits)"}
```

## Review Checklist

Analyze the changes across these dimensions:

### 1. Code Quality
- **Bugs & Logic Errors**: Are there obvious bugs or logic errors?
- **Best Practices**: Does code follow language/framework best practices?
- **Error Handling**: Is error handling appropriate and comprehensive?
- **Code Clarity**: Is the code easy to understand? Are names meaningful?

### 2. Design & Architecture
- **Approach**: Is the approach sound and well-suited to the problem?
- **Simplicity**: Are there simpler alternatives that achieve the same goal?
- **Tech Debt**: Does this introduce or reduce technical debt?
- **Separation of Concerns**: Are responsibilities properly separated?

### 3. Testing
- **Coverage**: Are there adequate tests for the changes?
- **Edge Cases**: Are edge cases and error paths covered?
- **Test Quality**: Are tests clear, maintainable, and properly isolated?

### 4. Security
- **Vulnerabilities**: Any obvious security vulnerabilities?
- **Input Validation**: Is user input properly validated and sanitized?
- **Secrets Handling**: Are secrets/credentials properly managed?
- **Authentication/Authorization**: Are auth checks present where needed?

### 5. Performance
- **Obvious Issues**: Any N+1 queries, unnecessary loops, or inefficient operations?
- **Scalability**: Will this approach scale with data/users?
- **Resource Usage**: Appropriate use of memory, CPU, network?

## Review Output Format

Provide a structured review:

**Summary** (2-3 sentences)
High-level assessment of the changes.

**Strengths** (optional)
What was done well.

**Issues** (if any)
For each issue:
- **Severity**: Critical / High / Medium / Low
- **Location**: File and line number(s)
- **Description**: What's wrong and why it matters
- **Suggestion**: How to fix it

**Approval Status**
- ✅ **Approve**: Changes are good to merge
- ⚠️ **Approve with Comments**: Minor issues, can merge but consider addressing
- 🔴 **Request Changes**: Blocking issues must be fixed before merge

## Additional Instructions

- Use filesystem:read_file to check full context if needed
- Use CAST_ANALYSIS for deep code analysis if relevant
- Use glob to find related test files
- Be specific: cite file names, line numbers, and code snippets
- Focus on actionable feedback, not style nitpicks (unless severe)
- Consider the broader context: recent commits, project patterns

Begin the code review now.
"""
