"""
Git tools for intelligent version control operations.
These tools wrap git commands with structured output for LLM consumption.
"""
import subprocess
import os
from typing import Optional, List
from pathlib import Path
from pydantic import Field

from aworld.core.tool.func_to_tool import be_tool


def _run_git_command(args: List[str], cwd: Optional[str] = None) -> tuple[bool, str, str]:
    """
    Execute a git command and return (success, stdout, stderr).
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Git command timed out after 30 seconds"
    except FileNotFoundError:
        return False, "", "Git is not installed or not in PATH"
    except Exception as e:
        return False, "", f"Error executing git command: {str(e)}"


def _check_git_repo() -> tuple[bool, str]:
    """Check if current directory is a git repository."""
    success, stdout, stderr = _run_git_command(["rev-parse", "--git-dir"])
    if not success:
        return False, "Not a git repository. Use 'git init' to create one."
    return True, ""


@be_tool(
    tool_name='git_status',
    tool_desc='Get structured git repository status showing untracked, modified, and staged files.'
)
def git_status() -> str:
    """
    Get git repository status with structured output.

    Returns a formatted status including:
    - Current branch
    - Untracked files
    - Modified files (unstaged)
    - Staged files (ready to commit)
    - Ahead/behind remote tracking branch
    """
    # Check if git repo
    is_repo, error_msg = _check_git_repo()
    if not is_repo:
        return error_msg

    # Get current branch
    success, branch, _ = _run_git_command(["branch", "--show-current"])
    branch_name = branch.strip() if success else "unknown"

    # Get porcelain status
    success, status_output, stderr = _run_git_command(["status", "--porcelain"])
    if not success:
        return f"Error getting git status: {stderr}"

    # Parse status
    untracked = []
    modified = []
    staged = []

    for line in status_output.strip().split("\n"):
        if not line:
            continue

        status_code = line[:2]
        filename = line[3:]

        # First character is staged status, second is unstaged
        if status_code[0] != " " and status_code[0] != "?":
            staged.append(filename)

        if status_code[1] == "M":
            modified.append(filename)

        if status_code == "??":
            untracked.append(filename)

    # Get ahead/behind info
    success, tracking, _ = _run_git_command(["rev-list", "--left-right", "--count", f"{branch_name}...@{{u}}"])
    ahead_behind = ""
    if success and tracking.strip():
        parts = tracking.strip().split()
        if len(parts) == 2:
            ahead, behind = parts
            if ahead != "0" or behind != "0":
                ahead_behind = f"\nBranch status: {ahead} ahead, {behind} behind remote"

    # Format output
    output = [f"On branch: {branch_name}"]

    if ahead_behind:
        output.append(ahead_behind)

    if staged:
        output.append(f"\nStaged files ({len(staged)}):")
        output.extend([f"  {f}" for f in staged])

    if modified:
        output.append(f"\nModified files ({len(modified)}):")
        output.extend([f"  {f}" for f in modified])

    if untracked:
        output.append(f"\nUntracked files ({len(untracked)}):")
        output.extend([f"  {f}" for f in untracked])

    if not staged and not modified and not untracked:
        output.append("\nWorking tree clean - no changes to commit")

    return "\n".join(output)


@be_tool(
    tool_name='git_diff',
    tool_desc='Show code changes (diff). Can show staged changes, unstaged changes, or compare against a specific ref.'
)
def git_diff(
    ref: Optional[str] = Field(None, description="Reference to compare against (e.g., 'HEAD', 'main', commit hash). If not specified, shows unstaged changes."),
    staged: bool = Field(False, description="If True, show only staged changes (--staged). Ignored if ref is specified."),
    file: Optional[str] = Field(None, description="Show diff for a specific file only")
) -> str:
    """
    Show git diff with various modes.

    Examples:
    - git_diff() → unstaged changes
    - git_diff(staged=True) → staged changes
    - git_diff(ref="HEAD") → all changes vs HEAD
    - git_diff(ref="main") → all changes vs main branch
    - git_diff(file="src/main.py") → changes in specific file
    """
    # Check if git repo
    is_repo, error_msg = _check_git_repo()
    if not is_repo:
        return error_msg

    # Build git diff command
    args = ["diff"]

    if ref:
        args.append(ref)
    elif staged:
        args.append("--staged")

    if file:
        args.append("--")
        args.append(file)

    success, diff_output, stderr = _run_git_command(args)

    if not success:
        return f"Error getting git diff: {stderr}"

    if not diff_output.strip():
        if staged:
            return "No staged changes"
        elif ref:
            return f"No changes compared to {ref}"
        else:
            return "No unstaged changes"

    # Add header for context
    mode = "staged changes" if staged else f"changes vs {ref}" if ref else "unstaged changes"
    header = f"=== Git Diff ({mode}) ===\n"

    # Limit output size (max 50KB)
    max_size = 50 * 1024
    if len(diff_output) > max_size:
        truncated = diff_output[:max_size]
        return f"{header}{truncated}\n\n... (diff truncated, showing first 50KB)"

    return f"{header}{diff_output}"


@be_tool(
    tool_name='git_log',
    tool_desc='Show commit history with customizable format and limits.'
)
def git_log(
    limit: int = Field(10, description="Number of commits to show (default: 10)"),
    oneline: bool = Field(True, description="Use compact one-line format (default: True)"),
    ref: Optional[str] = Field(None, description="Show log for specific ref/branch (e.g., 'main', 'HEAD~5')")
) -> str:
    """
    Show git commit history.

    Examples:
    - git_log() → last 10 commits, one-line format
    - git_log(limit=20) → last 20 commits
    - git_log(oneline=False) → full commit messages
    - git_log(ref="main") → commits on main branch
    """
    # Check if git repo
    is_repo, error_msg = _check_git_repo()
    if not is_repo:
        return error_msg

    # Build command
    args = ["log", f"-{limit}"]

    if oneline:
        args.append("--oneline")
    else:
        args.extend(["--pretty=format:%h - %an, %ar : %s"])

    if ref:
        args.append(ref)

    success, log_output, stderr = _run_git_command(args)

    if not success:
        return f"Error getting git log: {stderr}"

    if not log_output.strip():
        return "No commits found"

    return log_output


@be_tool(
    tool_name='git_commit',
    tool_desc='Create a git commit with automatic staging. Use this after analyzing changes with git_status and git_diff.'
)
def git_commit(
    message: str = Field(description="Commit message (use clear, concise description of changes)"),
    files: Optional[List[str]] = Field(None, description="Specific files to commit. If not provided, commits all staged files.")
) -> str:
    """
    Create a git commit with optional file staging.

    Workflow:
    1. If files specified, stage them first (git add)
    2. Create commit with message
    3. Return commit hash and summary

    Examples:
    - git_commit("Fix login bug") → commit all staged files
    - git_commit("Add tests", files=["test_login.py"]) → stage and commit specific file
    """
    # Check if git repo
    is_repo, error_msg = _check_git_repo()
    if not is_repo:
        return error_msg

    # Stage files if specified
    if files:
        for file in files:
            success, _, stderr = _run_git_command(["add", file])
            if not success:
                return f"Error staging file {file}: {stderr}"

    # Check if there are staged changes
    success, status, _ = _run_git_command(["diff", "--cached", "--quiet"])
    if success:  # exit code 0 means no changes
        return "No staged changes to commit. Stage files first with 'git add' or specify files parameter."

    # Create commit
    success, commit_output, stderr = _run_git_command(["commit", "-m", message])

    if not success:
        # Check for common errors
        if "nothing to commit" in stderr:
            return "Nothing to commit - no staged changes"
        elif "Please tell me who you are" in stderr:
            return "Git user not configured. Run: git config user.name/user.email"
        else:
            return f"Error creating commit: {stderr}"

    # Get commit hash
    success, hash_output, _ = _run_git_command(["rev-parse", "HEAD"])
    commit_hash = hash_output.strip()[:8] if success else "unknown"

    return f"✓ Commit created: {commit_hash}\n{commit_output}"


@be_tool(
    tool_name='git_blame',
    tool_desc='Show line-by-line authorship for a file (who last modified each line).'
)
def git_blame(
    file: str = Field(description="File path to show blame for"),
    line_start: Optional[int] = Field(None, description="Start line number (1-based)"),
    line_end: Optional[int] = Field(None, description="End line number (1-based)")
) -> str:
    """
    Show git blame for a file (who last modified each line).

    Examples:
    - git_blame("src/main.py") → blame for entire file
    - git_blame("src/main.py", line_start=10, line_end=20) → blame for lines 10-20
    """
    # Check if git repo
    is_repo, error_msg = _check_git_repo()
    if not is_repo:
        return error_msg

    # Check if file exists
    if not Path(file).exists():
        return f"File not found: {file}"

    # Build command
    args = ["blame", "--line-porcelain"]

    if line_start and line_end:
        args.append(f"-L{line_start},{line_end}")
    elif line_start:
        args.append(f"-L{line_start},+1")

    args.append(file)

    success, blame_output, stderr = _run_git_command(args)

    if not success:
        return f"Error getting git blame: {stderr}"

    # Parse porcelain format and create readable output
    lines = []
    current_commit = {}

    for line in blame_output.split("\n"):
        if line.startswith("author "):
            current_commit["author"] = line[7:]
        elif line.startswith("author-time "):
            import datetime
            timestamp = int(line[12:])
            dt = datetime.datetime.fromtimestamp(timestamp)
            current_commit["date"] = dt.strftime("%Y-%m-%d %H:%M")
        elif line.startswith("\t"):
            # This is the actual code line
            code = line[1:]
            author = current_commit.get("author", "Unknown")
            date = current_commit.get("date", "Unknown")
            lines.append(f"{author:<20} {date:<16} | {code}")
            current_commit = {}

    if not lines:
        return f"No blame information for {file}"

    # Limit output
    if len(lines) > 500:
        lines = lines[:500]
        lines.append("\n... (output truncated, showing first 500 lines)")

    return "\n".join(lines)
