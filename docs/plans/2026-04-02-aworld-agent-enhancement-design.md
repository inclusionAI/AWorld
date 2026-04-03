# AWorld Agent Enhancement Design

**Date**: 2026-04-02  
**Author**: Claude + User  
**Status**: Draft  
**Branch**: feat/aworld-agent-enhancement

---

## 1. Executive Summary

### 1.1 Project Goal

This design document outlines a comprehensive enhancement plan for the AWorld built-in agent (Aworld agent) to achieve capabilities comparable to Claude Code, while maintaining AWorld's harness-first philosophy. The enhancement focuses on three core areas:

1. **Tool Ecosystem Enhancement**: Expose existing filesystem tools and add missing Git/Glob capabilities
2. **Slash Command System**: Implement efficient prompt-based command framework for high-frequency operations
3. **UI/UX Improvements**: Enhance terminal output, progress indicators, and error handling

The primary goal is to transform the Aworld agent from a capable code assistant into a powerful, Claude Code-level development agent with superior code understanding (via CAST), multi-agent coordination (via TeamSwarm), and streamlined developer workflows.

### 1.2 Current State Analysis

**Existing Strengths:**
- **Sandbox Architecture**: Complete filesystem abstraction with 13 MCP tools (read_file, write_file, edit_file, search_content, etc.)
- **CAST Tools**: Advanced AST-based code analysis and modification (CAST_ANALYSIS, CAST_CODER, CAST_SEARCH)
- **Multi-Agent System**: TeamSwarm with specialized sub-agents (developer, evaluator, diffusion, audio)
- **Terminal Execution**: Safe command execution via terminal MCP server

**Critical Gaps:**
- **Hidden Tools**: Filesystem tools exist in sandbox but are not exposed to agents (`builtin_tools` not configured)
- **No Git Tooling**: Git operations require raw terminal commands; no intelligent abstractions
- **No Glob Tool**: File discovery relies on search_content; missing fast pattern matching
- **No Slash Commands**: All operations require verbose natural language descriptions
- **Basic UI**: Limited terminal feedback, no progress indicators, minimal error context

**Competitive Positioning:**
- Claude Code: 40+ tools, 103 slash commands, rich terminal UI, mature Git workflow
- AWorld Agent: Strong architecture + hidden capabilities → needs exposure and usability layer

---

## 2. Architecture Design

### 2.1 Overall Architecture Philosophy

**Design Principle: Harness-First, Not Framework-First**

AWorld operates at the **agent harness layer**, providing pre-configured orchestration rather than building blocks. This design maintains that philosophy:

- **No Reinvention**: Leverage existing sandbox infrastructure
- **Exposure Over Creation**: Reveal hidden capabilities before building new ones
- **Prompt-Based Commands**: Follow Claude Code's pattern of dynamic prompt generation, not hardcoded logic
- **Progressive Enhancement**: Three phases from quick wins to full feature parity

**Architectural Layers (Invocation Order):**

```
┌─────────────────────────────────────────────────────────────┐
│  User Interface Layer (CLI)                                  │
│  - Slash command parser                                      │
│  - REPL with command detection                               │
│  - User input handling                                       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Command Layer (NEW)                                         │
│  - Command registry and routing                              │
│  - Dynamic prompt generation                                 │
│  - Permission rule injection                                 │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Agent Layer (UNCHANGED)                                     │
│  - Aworld agent (TeamSwarm coordinator)                      │
│  - Sub-agents: developer, evaluator, diffusion, audio        │
│  - Receives prompts, decides which tools to call             │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Sandbox Layer (UNCHANGED)                                   │
│  - Provides isolated execution environment                   │
│  - Manages MCP server connections                            │
│  - Routes tool calls to appropriate implementations          │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Tool Layer (ENHANCED)                                       │
│  - Existing: CAST tools, terminal MCP, context tool          │
│  - Exposed: filesystem MCP (via builtin_tools)               │
│  - New: glob_tool, git_status, git_diff, git_commit, etc.   │
│  - Executes and returns results to Agent                     │
└─────────────────────────────────────────────────────────────┘
```

**Key Insight**: 
- **Invocation Flow**: UI → Command → Agent → Sandbox → Tool
- **Only Command Layer is new**: Adds slash command support
- **Tool Layer is enhanced**: Exposes hidden tools + adds new Git/Glob tools
- **Agent and Sandbox remain unchanged**: Stable foundation

### 2.2 Three-Phase Implementation Strategy

#### **Phase 1: Expose Hidden Capabilities** ⚡ (Day 1)

**Goal**: Unlock existing filesystem tools with minimal code changes

**Changes:**
- Add `builtin_tools=["filesystem", "terminal"]` to sandbox initialization
- Register filesystem MCP server in agent mcp_config
- Update agent tool_names to include filesystem tools

**Impact:**
- +13 tools immediately available
- Zero new code (pure configuration)
- Validates sandbox architecture works end-to-end

**Success Criteria:**
- Agent can call: read_file, write_file, edit_file, search_content, list_directory, create_directory, move_file, parse_file
- Filesystem tools show up in agent tool list
- No regression in existing functionality

#### **Phase 2: Add Missing Core Tools** 🔧 (Days 2-4)

**Goal**: Fill critical gaps in file discovery and version control

**New Tools:**

1. **Glob Tool** (Fast file pattern matching)
   - Purpose: `find . -name "*.py"` equivalent without shell
   - Input: pattern (str), path (Optional[str])
   - Output: List of matching file paths
   - Integration: Via `@be_tool` decorator

2. **Git Tool Suite** (5 tools for version control)
   - `git_status`: Structured git status (untracked, modified, staged)
   - `git_diff`: Show changes (staged, unstaged, or vs ref)
   - `git_log`: Formatted commit history with customizable limits
   - `git_commit`: Intelligent commit workflow (analyze → stage → commit)
   - `git_blame`: Code attribution for specific file/line

**Implementation Approach:**
- Use `@be_tool` decorator for simple function-to-tool conversion
- Wrap subprocess calls to git commands
- Return structured, LLM-friendly text output
- Handle errors gracefully (no git repo, merge conflicts, etc.)

**Success Criteria:**
- All 6 tools (glob + 5 git) callable by agent
- Tools return parseable, actionable output
- Error messages guide agent to correct usage

#### **Phase 3: Slash Command System** ⌨️ (Days 5-7)

**Goal**: Implement efficient command shortcuts for high-frequency operations

**Command Framework:**

```python
# Core abstractions
class Command:
    name: str
    description: str
    allowed_tools: List[str]  # Restrict tool access
    
    async def get_prompt(self, args: str, context: ToolUseContext) -> str:
        """Generate dynamic prompt based on current state"""

class CommandRegistry:
    """Central registry for all slash commands"""
    _commands: Dict[str, Command] = {}
    
    @classmethod
    def register(cls, cmd: Command):
        cls._commands[cmd.name] = cmd
    
    @classmethod
    def execute(cls, name: str, args: str, context: ToolUseContext):
        """Route to appropriate command handler"""
```

**Priority Commands:**

1. **/commit** - Smart git commit workflow
   - Context gathering: `git status`, `git diff HEAD`, recent commits
   - Prompt template: Analyze changes → draft message → commit with HEREDOC
   - Allowed tools: `["terminal:git*", "git_status", "git_diff", "git_commit"]`

2. **/review** - Code review assistant
   - Context: Changed files, full diff, recent commits
   - Prompt: Check for bugs, best practices, performance, security
   - Allowed tools: `["git_diff", "git_log", "read_file"]`

3. **/diff** - Quick diff viewer
   - Args: `[ref]` (default: HEAD)
   - Context: Diff output
   - Prompt: Summarize key changes
   - Allowed tools: `["git_diff"]`

**CLI Integration:**

```python
# In REPL loop
async def handle_user_input(user_input: str, context: ToolUseContext):
    if user_input.startswith("/"):
        # Parse command
        cmd_name, *args = user_input[1:].split(maxsplit=1)
        command = CommandRegistry.get(cmd_name)
        
        if command:
            # Generate prompt
            prompt = await command.get_prompt(args[0] if args else "", context)
            
            # Inject permission rules
            context = inject_command_permissions(context, command.allowed_tools)
            
            # Execute via agent
            return await run_agent_with_prompt(prompt, context)
    
    # Normal natural language handling
    return await run_agent(user_input, context)
```

**Success Criteria:**
- `/commit`, `/review`, `/diff` commands functional
- Commands auto-allow their tool whitelist (no manual approval)
- Command framework extensible for future additions

### 2.3 Command Types: Tool vs Prompt Commands

**Critical Distinction**: Commands fall into two categories with different execution flows:

#### **2.3.1 Tool Commands (Direct Execution)**

Commands that execute logic directly without involving the agent.

**Examples**: `/help`, `/config`, `/list`, `/mcp`

**Flow:**
```
User types "/help"
  ↓
CLI detects "/" prefix
  ↓
CommandRegistry.get("help")
  ↓
Command.execute() ← Direct execution
  ↓
Result returned to user (no agent involved)
```

**Characteristics:**
- Deterministic behavior
- Fast execution (no LLM call)
- Used for CLI/system operations
- No `allowed_tools` needed

**Architecture:**
```
UI → Command → Tool (Direct)
```

#### **2.3.2 Prompt Commands (Agent-Mediated)**

Commands that generate prompts for the agent to execute using tools.

**Examples**: `/commit`, `/review`, `/diff`

**Flow:**
```
User types "/commit"
  ↓
CLI detects "/" prefix
  ↓
CommandRegistry.get("commit")
  ↓
CommitCommand.get_prompt()
  ├─ Gathers context: git status, git diff, git log
  ├─ Generates: Structured prompt with instructions
  └─ Returns: "Based on these changes, create a commit..."
  ↓
inject_command_permissions(allowed_tools=["git*"])
  ↓
Agent receives prompt (with context + permission rules)
  ↓
Agent decides which tools to call
  ↓
Agent → Sandbox → Agent Tools (git_commit, terminal:git, etc.)
  ↓
Result returned to user
```

**Characteristics:**
- Leverages agent intelligence
- Requires LLM call
- Used for complex workflows
- Needs `allowed_tools` whitelist

**Architecture:**
```
UI → Command (Prompt Generator) → Agent → Sandbox → Agent Tools
```

### 2.3 Component Interaction Diagram

**Normal Flow (Natural Language):**
```
User → CLI REPL → Agent → Sandbox → Agent Tools → Result
```

**Tool Command Flow (Direct):**
```
User → CLI → Command (executes directly) → Result
```

**Prompt Command Flow (Agent-Mediated):**
```
User → CLI → Command (generates prompt) → Agent → Sandbox → Agent Tools → Result
```

**Key Architectural Insight:**
- **Tool Commands**: Bypass agent, execute immediately (like CLI utilities)
- **Prompt Commands**: Delegate to agent, leverage LLM intelligence (like natural language)
- Both command types coexist in the same registry
- Command type determined by whether `execute()` or `get_prompt()` is implemented

**Benefits of This Design:**
- **Flexibility**: Simple commands can be fast (direct), complex commands can be smart (agent)
- **Consistency**: All commands accessed via `/` prefix
- **Extensibility**: New commands choose execution style based on needs
- **Efficiency**: No LLM call for trivial operations (/help, /config)
- **Intelligence**: Complex workflows benefit from agent reasoning (/commit, /review)

---

## 3. Detailed Implementation Plan

### 3.1 Phase 1: Expose Filesystem Tools (Day 1)

#### **3.1.1 Modify Developer Agent**

**File**: `aworld-cli/src/aworld_cli/inner_plugins/smllc/agents/developer/developer.py`

**Current Code:**
```python
sandbox = Sandbox(
    mcp_config=mcp_config
)
sandbox.reuse = True
```

**Updated Code:**
```python
sandbox = Sandbox(
    mcp_config=mcp_config,
    builtin_tools=["filesystem", "terminal"]  # ✅ Enable builtin tools
)
sandbox.reuse = True
```

**Rationale:**
- `builtin_tools` parameter triggers `ToolConfigManager` to auto-configure filesystem and terminal MCP servers
- MCP servers are started as stdio subprocesses pointing to `aworld/sandbox/tool_servers/`
- Tools become available through the sandbox's MCP client integration

#### **3.1.2 Modify Aworld Agent**

**File**: `aworld-cli/src/aworld_cli/inner_plugins/smllc/agents/aworld_agent.py`

**Current Code:**
```python
aworld_agent = Agent(
    name="Aworld",
    desc="...",
    conf=agent_config,
    system_prompt=...,
    mcp_servers=["terminal"],
    mcp_config={...},
    tool_names=[CONTEXT_TOOL, 'CAST_SEARCH']
)
```

**Updated Code:**
```python
# Create sandbox with builtin tools
from aworld.sandbox import Sandbox
sandbox = Sandbox(
    mcp_config={
        "mcpServers": {
            "terminal": {
                "command": sys.executable,
                "args": ["-m", "examples.gaia.mcp_collections.tools.terminal"],
                "env": {},
                "client_session_timeout_seconds": 9999.0,
            }
        }
    },
    builtin_tools=["filesystem", "terminal"]  # ✅ Enable builtin tools
)
sandbox.reuse = True

aworld_agent = Agent(
    name="Aworld",
    desc="...",
    conf=agent_config,
    system_prompt=...,
    mcp_servers=["terminal", "filesystem"],  # ✅ Add filesystem
    sandbox=sandbox,  # ✅ Pass sandbox instance
    tool_names=[CONTEXT_TOOL, 'CAST_SEARCH']
)
```

**Key Changes:**
1. Create sandbox instance with `builtin_tools=["filesystem", "terminal"]`
2. Add `"filesystem"` to `mcp_servers` list
3. Pass `sandbox=sandbox` to Agent constructor

#### **3.1.3 Verification Steps**

1. **Start agent**: `aworld-cli`
2. **Check available tools**: Agent should list filesystem tools
3. **Test file operations**:
   ```
   User: Read the file README.md
   Agent: [calls filesystem:read_file tool]
   
   User: List files in the current directory
   Agent: [calls filesystem:list_directory tool]
   
   User: Search for "TODO" in all Python files
   Agent: [calls filesystem:search_content tool]
   ```

**Expected Outcome:**
- 13 filesystem tools available: read_file, write_file, edit_file, create_directory, list_directory, move_file, upload_file, download_file, parse_file, search_content, list_allowed_directories, read_media_file
- No code changes to sandbox layer
- No regression in existing functionality

---

### 3.2 Phase 2: Add Core Tools (Days 2-4)

#### **3.2.1 Glob Tool Implementation**

**File**: `aworld/tools/glob_tool.py` (NEW)

```python
"""
Glob tool for fast file pattern matching.
Provides a simpler alternative to search_content when only filenames matter.
"""
from pathlib import Path
from typing import Optional, List
from pydantic import Field

from aworld.core.tool.func_to_tool import be_tool


@be_tool(
    tool_name='glob',
    tool_desc='Find files matching a glob pattern (e.g., *.py, src/**/*.ts). '
              'Faster than search_content when you only need filenames, not content.'
)
def glob_search(
    pattern: str = Field(description="Glob pattern to match files against (e.g., '*.py', 'src/**/*.ts', 'test_*.py')"),
    path: Optional[str] = Field(None, description="Directory to search in. Defaults to current working directory if not specified.")
) -> str:
    """
    Find files matching a glob pattern.
    
    Returns a newline-separated list of relative file paths that match the pattern.
    Results are sorted by modification time (most recent first).
    
    Examples:
    - glob("*.py") → All Python files in current directory
    - glob("src/**/*.ts") → All TypeScript files in src/ recursively
    - glob("test_*.py", "tests/") → Test files in tests/ directory
    """
    import os
    from pathlib import Path
    
    # Determine search path
    search_path = Path(path) if path else Path.cwd()
    
    if not search_path.exists():
        return f"Error: Path does not exist: {search_path}"
    
    if not search_path.is_dir():
        return f"Error: Path is not a directory: {search_path}"
    
    try:
        # Use glob to find matches
        matches = list(search_path.glob(pattern))
        
        if not matches:
            return f"No files found matching pattern: {pattern}"
        
        # Sort by modification time (most recent first)
        matches_with_mtime = [(m, m.stat().st_mtime) for m in matches if m.is_file()]
        matches_with_mtime.sort(key=lambda x: x[1], reverse=True)
        
        # Convert to relative paths
        relative_paths = []
        for match, _ in matches_with_mtime:
            try:
                rel_path = match.relative_to(Path.cwd())
                relative_paths.append(str(rel_path))
            except ValueError:
                # If not relative to cwd, use absolute path
                relative_paths.append(str(match))
        
        # Format output
        result = "\n".join(relative_paths)
        count = len(relative_paths)
        
        if count > 100:
            result += f"\n\n(Showing {count} files. Consider using a more specific pattern.)"
        
        return result
    
    except Exception as e:
        return f"Error searching with pattern '{pattern}': {str(e)}"
```

**Integration:**
```python
# In aworld_agent.py
tool_names=[CONTEXT_TOOL, 'CAST_SEARCH', 'glob']  # Add glob
```

#### **3.2.2 Git Tools Implementation**

**File**: `aworld/tools/git_tools.py` (NEW)

```python
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
```

**Integration:**
```python
# In aworld_agent.py or developer.py
tool_names=[CONTEXT_TOOL, 'CAST_SEARCH', 'glob', 
            'git_status', 'git_diff', 'git_log', 'git_commit', 'git_blame']
```

#### **3.2.3 Testing Phase 2 Tools**

**Test Scenarios:**

1. **Glob Tool:**
   ```
   User: Find all Python test files
   Agent: [calls glob("test_*.py")]
   
   User: List all TypeScript files in src directory
   Agent: [calls glob("**/*.ts", "src")]
   ```

2. **Git Tools:**
   ```
   User: What's the current git status?
   Agent: [calls git_status()]
   
   User: Show me what changed since last commit
   Agent: [calls git_diff()]
   
   User: What are the last 5 commits?
   Agent: [calls git_log(limit=5)]
   ```

**Success Criteria:**
- All 6 new tools callable
- Tools return structured, LLM-friendly output
- Error handling for non-git repos, missing files, etc.

---

### 3.3 Phase 3: Slash Command System (Days 5-7)

#### **3.3.1 Command Framework Implementation**

**File**: `aworld-cli/src/aworld_cli/core/command_system.py` (NEW)

```python
"""
Slash command framework for AWorld CLI.
Implements prompt-based commands following Claude Code's pattern.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class CommandContext:
    """Context passed to command handlers"""
    cwd: str
    agent_config: Any
    sandbox: Any
    user_args: str


class Command(ABC):
    """
    Base class for slash commands.
    
    Commands can be either:
    1. Tool Commands: Direct execution (implement execute())
    2. Prompt Commands: Agent-mediated (implement get_prompt())
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Command name (without / prefix)"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Short description shown in help"""
        pass
    
    @property
    def command_type(self) -> str:
        """
        Command type: 'tool' or 'prompt'.
        - 'tool': Direct execution (no agent)
        - 'prompt': Agent-mediated execution
        """
        return "prompt"  # Default to prompt commands
    
    @property
    def allowed_tools(self) -> List[str]:
        """
        Whitelist of tools this command can use (for prompt commands).
        Supports wildcards: "terminal:git*" allows all git commands.
        Ignored for tool commands.
        """
        return []
    
    async def execute(self, context: CommandContext) -> str:
        """
        Direct execution for tool commands.
        Override this for commands that don't need agent involvement.
        
        Example: /help, /config, /list
        """
        raise NotImplementedError(f"Command {self.name} does not support direct execution")
    
    async def get_prompt(self, context: CommandContext) -> str:
        """
        Generate prompt for agent-mediated commands.
        Override this for commands that leverage agent intelligence.
        
        Example: /commit, /review, /diff
        """
        raise NotImplementedError(f"Command {self.name} does not support prompt generation")
    
    async def pre_execute(self, context: CommandContext) -> Optional[str]:
        """
        Optional hook before execution (both types).
        Return error message if command cannot proceed.
        """
        return None
    
    async def post_execute(self, context: CommandContext, result: Any) -> None:
        """Optional hook after execution (both types)"""
        pass


class CommandRegistry:
    """
    Central registry for all slash commands.
    Commands are registered at module import time.
    """
    
    _commands: Dict[str, Command] = {}
    
    @classmethod
    def register(cls, command: Command) -> None:
        """Register a command"""
        if command.name in cls._commands:
            raise ValueError(f"Command '{command.name}' already registered")
        cls._commands[command.name] = command
    
    @classmethod
    def get(cls, name: str) -> Optional[Command]:
        """Get command by name"""
        return cls._commands.get(name)
    
    @classmethod
    def list_commands(cls) -> List[Command]:
        """Get all registered commands"""
        return list(cls._commands.values())
    
    @classmethod
    def help_text(cls) -> str:
        """Generate help text for all commands"""
        lines = ["Available commands:"]
        for cmd in sorted(cls._commands.values(), key=lambda c: c.name):
            lines.append(f"  /{cmd.name:<15} {cmd.description}")
        return "\n".join(lines)


def register_command(cmd: Command) -> Command:
    """Decorator to register a command"""
    CommandRegistry.register(cmd)
    return cmd
```

#### **3.3.2 Command Implementations**

**File**: `aworld-cli/src/aworld_cli/commands/commit.py` (NEW)

```python
"""
/commit command - Smart git commit workflow
"""
import subprocess
from aworld_cli.core.command_system import Command, CommandContext, register_command


@register_command
class CommitCommand(Command):
    @property
    def name(self) -> str:
        return "commit"
    
    @property
    def description(self) -> str:
        return "Create a git commit with intelligent message generation"
    
    @property
    def allowed_tools(self) -> List[str]:
        return [
            "terminal:git add*",
            "terminal:git status*",
            "terminal:git commit*",
            "terminal:git log*",
            "terminal:git diff*",
            "git_status",
            "git_diff",
            "git_log",
            "git_commit"
        ]
    
    async def pre_execute(self, context: CommandContext) -> Optional[str]:
        """Check if we're in a git repo"""
        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=context.cwd,
                capture_output=True,
                check=True
            )
            return None
        except:
            return "Not a git repository. Run 'git init' to create one."
    
    async def get_prompt(self, context: CommandContext) -> str:
        """Generate commit prompt with context"""
        
        # Execute context-gathering commands
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=context.cwd,
            capture_output=True,
            text=True
        ).stdout
        
        diff = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=context.cwd,
            capture_output=True,
            text=True
        ).stdout
        
        recent_commits = subprocess.run(
            ["git", "log", "--oneline", "-10"],
            cwd=context.cwd,
            capture_output=True,
            text=True
        ).stdout
        
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=context.cwd,
            capture_output=True,
            text=True
        ).stdout.strip()
        
        return f"""## Git Commit Task

You are creating a git commit. Follow this process:

### 1. Current Context

**Branch**: {branch}

**Status**:
```
{status}
```

**Changes (diff)**:
```
{diff[:10000]}  # Limit diff size
```

**Recent Commits** (for style reference):
```
{recent_commits}
```

### 2. Analysis

Analyze the changes and determine:
- Type of change: feat / fix / refactor / docs / test / chore
- Scope: What component/module is affected?
- Impact: What problem does this solve?

### 3. Create Commit

Follow these rules:
- NEVER use `git commit --amend` (always create NEW commits)
- NEVER skip hooks (--no-verify)
- NEVER commit secrets (.env, credentials, etc.)
- Draft a concise 1-2 sentence message focusing on WHY not WHAT
- Follow this repository's commit style (see recent commits above)

Use HEREDOC format for commit message:
```bash
git commit -m "$(cat <<'EOF'
Your commit message here.
EOF
)"
```

### 4. Execute

1. Stage relevant files if needed: `git add <files>`
2. Create the commit using the HEREDOC format above
3. Report the commit hash

Begin now. Call only git-related tools. Do not explain your steps - just execute them.
"""


# Similar structure for other commands...
```

**File**: `aworld-cli/src/aworld_cli/commands/review.py` (NEW)

```python
@register_command
class ReviewCommand(Command):
    @property
    def name(self) -> str:
        return "review"
    
    @property
    def description(self) -> str:
        return "Perform code review on current changes"
    
    @property
    def allowed_tools(self) -> List[str]:
        return ["git_diff", "git_log", "read_file", "glob"]
    
    async def get_prompt(self, context: CommandContext) -> str:
        # Get changed files
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=context.cwd,
            capture_output=True,
            text=True
        )
        changed_files = result.stdout.strip().split("\n")
        
        # Get full diff
        diff = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=context.cwd,
            capture_output=True,
            text=True
        ).stdout
        
        return f"""## Code Review Task

Review the following changes and provide feedback.

**Changed Files**:
{chr(10).join(f"- {f}" for f in changed_files if f)}

**Full Diff**:
```diff
{diff[:20000]}  # Limit size
```

**Review Checklist**:

1. **Code Quality**
   - Are there any obvious bugs or logic errors?
   - Does the code follow best practices?
   - Is error handling appropriate?

2. **Design & Architecture**
   - Is the approach sound?
   - Are there simpler alternatives?
   - Does it introduce tech debt?

3. **Testing**
   - Are there adequate tests?
   - Are edge cases covered?

4. **Security**
   - Any security vulnerabilities?
   - Input validation present?
   - Secrets properly handled?

5. **Performance**
   - Any obvious performance issues?
   - Unnecessary loops or operations?

Provide a structured review with:
- Summary (1-2 sentences)
- Issues found (if any) with severity and line references
- Suggestions for improvement
- Approval status (Approve / Needs Changes / Request Changes)

Use file reading tools to check full context if needed.
"""
```

**File**: `aworld-cli/src/aworld_cli/commands/diff.py` (NEW)

```python
@register_command
class DiffCommand(Command):
    @property
    def name(self) -> str:
        return "diff"
    
    @property
    def description(self) -> str:
        return "Show and summarize code changes"
    
    @property
    def allowed_tools(self) -> List[str]:
        return ["git_diff", "git_log"]
    
    async def get_prompt(self, context: CommandContext) -> str:
        ref = context.user_args.strip() or "HEAD"
        
        # Get diff
        diff = subprocess.run(
            ["git", "diff", ref],
            cwd=context.cwd,
            capture_output=True,
            text=True
        ).stdout
        
        # Get changed files count
        files = subprocess.run(
            ["git", "diff", "--name-only", ref],
            cwd=context.cwd,
            capture_output=True,
            text=True
        ).stdout.strip().split("\n")
        
        return f"""## Diff Summary Task

Compare current state with `{ref}`.

**Changed Files** ({len(files)}):
{chr(10).join(f"- {f}" for f in files if f)}

**Full Diff**:
```diff
{diff[:30000]}
```

Provide a structured summary:
1. **Overview**: High-level description (1-2 sentences)
2. **Key Changes**: Bullet list of major modifications
3. **Files Affected**: Group changes by file with brief descriptions
4. **Impact**: What breaks? What's new? What's deprecated?

Be concise but comprehensive.
"""
```

#### **3.3.3 CLI Integration**

**File**: `aworld-cli/src/aworld_cli/main.py` (MODIFIED)

```python
from aworld_cli.core.command_system import CommandRegistry, CommandContext
from aworld_cli.commands import commit, review, diff  # Import to trigger @register_command

async def handle_user_input(user_input: str, context: ToolUseContext):
    """Enhanced REPL handler with command support (both tool and prompt types)"""
    
    # Check for slash command
    if user_input.startswith("/"):
        # Parse command
        parts = user_input[1:].split(maxsplit=1)
        cmd_name = parts[0]
        cmd_args = parts[1] if len(parts) > 1 else ""
        
        # Get command
        command = CommandRegistry.get(cmd_name)
        if not command:
            print(f"Unknown command: /{cmd_name}")
            print("Type /help for available commands")
            return
        
        # Build command context
        cmd_context = CommandContext(
            cwd=os.getcwd(),
            agent_config=context.options,
            sandbox=context.sandbox,
            user_args=cmd_args
        )
        
        # Pre-execute check (both types)
        error = await command.pre_execute(cmd_context)
        if error:
            print(f"Error: {error}")
            return
        
        # Route based on command type
        if command.command_type == "tool":
            # ===== Tool Command: Direct Execution =====
            # Flow: Command → Direct Tool (no agent)
            print(f"Executing /{cmd_name}...")
            result = await command.execute(cmd_context)
            
            # Post-execute hook
            await command.post_execute(cmd_context, result)
            
            # Display result
            print(result)
            return result
        
        else:  # command_type == "prompt"
            # ===== Prompt Command: Agent-Mediated Execution =====
            # Flow: Command → Agent → Sandbox → Agent Tools
            
            # Generate prompt for agent
            prompt = await command.get_prompt(cmd_context)
            
            # Inject permission rules for allowed tools
            modified_context = inject_command_permissions(context, command.allowed_tools)
            
            # Execute via agent
            print(f"Executing /{cmd_name}...")
            result = await run_agent_with_prompt(prompt, modified_context)
            
            # Post-execute hook
            await command.post_execute(cmd_context, result)
            
            return result
    
    # Normal natural language processing
    return await run_agent(user_input, context)


def inject_command_permissions(context: ToolUseContext, allowed_tools: List[str]) -> ToolUseContext:
    """
    Modify context to auto-allow command tools.
    This prevents permission prompts for whitelisted tools.
    """
    # Clone context
    new_context = copy.deepcopy(context)
    
    # Add allowed_tools to alwaysAllowRules
    if not hasattr(new_context, 'tool_permission_context'):
        new_context.tool_permission_context = {}
    
    if 'alwaysAllowRules' not in new_context.tool_permission_context:
        new_context.tool_permission_context['alwaysAllowRules'] = {}
    
    new_context.tool_permission_context['alwaysAllowRules']['command'] = allowed_tools
    
    return new_context
```

#### **3.3.4 Command Type Examples**

**Example 1: Tool Command (/help)**

```python
@register_command
class HelpCommand(Command):
    @property
    def name(self) -> str:
        return "help"
    
    @property
    def description(self) -> str:
        return "Show available commands"
    
    @property
    def command_type(self) -> str:
        return "tool"  # Direct execution, no agent
    
    async def execute(self, context: CommandContext) -> str:
        """Direct execution - instant response"""
        return CommandRegistry.help_text()

# Usage: /help
# Flow: CLI → HelpCommand.execute() → Display result
# Characteristics: Fast, deterministic, no LLM call
```

**Example 2: Prompt Command (/commit)**

```python
@register_command
class CommitCommand(Command):
    @property
    def name(self) -> str:
        return "commit"
    
    @property
    def description(self) -> str:
        return "Create a git commit with intelligent message generation"
    
    @property
    def command_type(self) -> str:
        return "prompt"  # Agent-mediated (default)
    
    @property
    def allowed_tools(self) -> List[str]:
        return ["terminal:git*", "git_status", "git_diff", "git_commit"]
    
    async def get_prompt(self, context: CommandContext) -> str:
        """Generate prompt for agent to execute"""
        # Gather git context
        status = subprocess.run(["git", "status", "--short"], ...).stdout
        diff = subprocess.run(["git", "diff", "HEAD"], ...).stdout
        
        return f"""## Git Commit Task
        
**Current Status:**
```
{status}
```

**Changes:**
```
{diff}
```

Analyze the changes and create a commit with an appropriate message.
Use git_commit() tool or terminal:git commands.
"""

# Usage: /commit
# Flow: CLI → CommitCommand.get_prompt() → Agent → git_commit() → Result
# Characteristics: Intelligent, flexible, requires LLM
```

#### **3.3.5 Testing Phase 3**

**Test Scenarios:**

1. **Tool Command (/help):**
   ```
   $ aworld-cli
   > /help
   Executing /help...
   
   Available commands:
     /commit         Create a git commit with intelligent message generation
     /review         Perform code review on current changes
     /diff           Show and summarize code changes
     /help           Show available commands
   
   # Fast response, no agent involved
   ```

2. **Commit Command:**
   ```
   > /commit
   Executing /commit...
   [Agent analyzes changes]
   [Agent creates commit]
   ✓ Commit created: a3f2b1c
   ```

3. **Review Command:**
   ```
   > /review
   Executing /review...
   [Agent reviews code]
   
   Summary: Changes look good overall...
   Issues: None found
   Approval: Approve
   ```

4. **Diff Command:**
   ```
   > /diff main
   Executing /diff...
   [Agent summarizes changes vs main]
   
   Overview: Added user authentication module
   Key Changes:
   - Added Auth class
   - Updated routes
   ...
   ```

**Success Criteria:**
- Commands execute without manual permission prompts
- Commands generate appropriate prompts with context
- Agent successfully completes tasks using allowed tools
- Error handling for edge cases (not a git repo, etc.)

---

## 4. Technical Trade-offs and Risks

### 4.1 Design Decisions

#### **Decision 1: Prompt-Based Commands vs Hardcoded Logic**

**Choice**: Prompt-based (following Claude Code pattern)

**Rationale:**
- **Pro**: Commands are easy to iterate (just change prompt text)
- **Pro**: Leverages agent's intelligence rather than hardcoding logic
- **Pro**: Naturally adapts to different scenarios
- **Con**: Less predictable than deterministic code
- **Con**: Requires LLM call for each command (cost + latency)

**Mitigation**: For commands that need deterministic behavior, we can add validation in `pre_execute()` hook.

#### **Decision 2: @be_tool Decorator vs Tool Class Hierarchy**

**Choice**: `@be_tool` decorator for new tools (glob, git_*)

**Rationale:**
- **Pro**: Simpler for function-based tools
- **Pro**: Less boilerplate code
- **Pro**: Consistent with existing AWorld patterns
- **Con**: Less flexible than class-based tools
- **Con**: Harder to add complex state management

**Mitigation**: For tools that need state or complex lifecycle, we can still use Tool class hierarchy.

#### **Decision 3: Expose Filesystem via builtin_tools vs Direct MCP Integration**

**Choice**: Use `builtin_tools` parameter

**Rationale:**
- **Pro**: Zero code changes to sandbox internals
- **Pro**: Leverages existing ToolConfigManager
- **Pro**: Easy to toggle on/off
- **Con**: Requires understanding of builtin_tools mechanism
- **Con**: Less explicit than direct MCP config

**Mitigation**: Document `builtin_tools` mechanism clearly in code comments.

#### **Decision 4: Git Tools via Subprocess vs GitPython Library**

**Choice**: Subprocess calls to git CLI

**Rationale:**
- **Pro**: No external dependencies
- **Pro**: Works everywhere git is installed
- **Pro**: Easier to debug (matches manual git commands)
- **Con**: Slower than library calls
- **Con**: Need to parse CLI output

**Mitigation**: Acceptable since git operations are not performance-critical.

### 4.2 Risks and Mitigation

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| **Filesystem tools conflict with terminal MCP** | Medium | Low | Both use different MCP server names; no conflict expected |
| **Command prompt injection vulnerability** | High | Low | Commands only inject whitelisted tools; prompt is generated server-side |
| **Git tools fail on non-git repos** | Low | High | Pre-execute checks return early with helpful error |
| **Output size limits for git diff** | Medium | Medium | Truncate large diffs with clear indication |
| **Permission system complexity** | Medium | Low | Start simple (auto-allow for commands); expand if needed |
| **Agent ignores command constraints** | Medium | Low | Use `allowed_tools` to enforce; add validation hooks |

### 4.3 Backward Compatibility

**No Breaking Changes:**
- All changes are additive
- Existing agents without `builtin_tools` work unchanged
- New tools are opt-in via `tool_names` registration
- Commands are new feature (no existing `/` command behavior to break)

**Migration Path:**
- Phase 1 can be deployed independently (just expose tools)
- Phase 2 can be deployed independently (just add new tools)
- Phase 3 requires Phase 1+2 but doesn't break existing functionality

---

## 5. Testing Strategy

### 5.1 Unit Tests

**Phase 1: Exposure Tests**
```python
# tests/test_builtin_tools_exposure.py

def test_filesystem_tools_available_when_enabled():
    """Verify filesystem tools show up when builtin_tools is set"""
    sandbox = Sandbox(builtin_tools=["filesystem"])
    tools = sandbox.list_available_tools()
    assert "filesystem:read_file" in tools
    assert "filesystem:write_file" in tools
    assert "filesystem:edit_file" in tools

def test_filesystem_tools_not_available_when_disabled():
    """Verify filesystem tools hidden when builtin_tools not set"""
    sandbox = Sandbox()
    tools = sandbox.list_available_tools()
    assert "filesystem:read_file" not in tools
```

**Phase 2: Tool Tests**
```python
# tests/tools/test_glob_tool.py

def test_glob_finds_python_files():
    result = glob_search("*.py", "tests/fixtures")
    assert "test_example.py" in result
    assert "helper.py" in result

def test_glob_handles_nonexistent_path():
    result = glob_search("*.py", "/nonexistent")
    assert "Error" in result
    assert "does not exist" in result

# tests/tools/test_git_tools.py

def test_git_status_in_git_repo():
    result = git_status()
    assert "On branch:" in result

def test_git_status_not_in_git_repo():
    result = git_status()
    assert "Not a git repository" in result

def test_git_commit_requires_staged_changes():
    result = git_commit("test message")
    assert "No staged changes" in result or "Commit created" in result
```

**Phase 3: Command Tests**
```python
# tests/commands/test_commit_command.py

@pytest.mark.asyncio
async def test_commit_command_generates_prompt():
    cmd = CommitCommand()
    context = CommandContext(cwd="/test/repo", ...)
    prompt = await cmd.get_prompt(context)
    assert "Git Commit Task" in prompt
    assert "Analysis" in prompt
    assert "Create Commit" in prompt

@pytest.mark.asyncio
async def test_commit_command_rejects_non_git_repo(tmp_path):
    cmd = CommitCommand()
    context = CommandContext(cwd=str(tmp_path), ...)
    error = await cmd.pre_execute(context)
    assert "Not a git repository" in error
```

### 5.2 Integration Tests

**End-to-End Agent Tests:**
```python
# tests/integration/test_agent_with_new_tools.py

@pytest.mark.asyncio
async def test_agent_can_read_file_via_filesystem_tool():
    """Verify agent can use filesystem tools after Phase 1"""
    agent = build_aworld_agent()  # With builtin_tools enabled
    result = await agent.run("Read the file README.md")
    assert result.tool_calls_made[0].tool_name == "filesystem:read_file"

@pytest.mark.asyncio
async def test_agent_can_use_glob_tool():
    """Verify agent can use new glob tool after Phase 2"""
    agent = build_aworld_agent()
    result = await agent.run("Find all Python test files")
    assert "glob" in [tc.tool_name for tc in result.tool_calls_made]

@pytest.mark.asyncio
async def test_commit_command_workflow():
    """Verify /commit command works end-to-end"""
    cli = AworldCLI()
    result = await cli.handle_input("/commit")
    assert "Commit created" in result or "No staged changes" in result
```

### 5.3 Manual Testing Checklist

**Phase 1 Validation:**
- [ ] Start aworld-cli with updated developer agent
- [ ] Verify filesystem tools show up in tool list
- [ ] Test: "Read the file README.md"
- [ ] Test: "List files in the current directory"
- [ ] Test: "Search for 'TODO' in all Python files"
- [ ] Verify no regression in existing functionality

**Phase 2 Validation:**
- [ ] Test glob tool: "Find all .ts files in src/"
- [ ] Test git_status: "What's the current git status?"
- [ ] Test git_diff: "Show me what changed"
- [ ] Test git_log: "Show the last 5 commits"
- [ ] Test git_commit: "Create a commit for these changes"
- [ ] Verify error handling for non-git repos

**Phase 3 Validation:**
- [ ] Test /help command
- [ ] Test /commit in a git repo with changes
- [ ] Test /commit in a repo with no changes
- [ ] Test /review with some code changes
- [ ] Test /diff with and without ref argument
- [ ] Verify commands don't trigger permission prompts
- [ ] Test command error handling

### 5.4 Performance Tests

**Metrics to Track:**
- Command latency: `/commit` should complete in < 10 seconds
- Tool call overhead: Filesystem tools should add < 100ms vs direct file access
- Memory usage: No significant memory leaks from repeated tool calls
- Concurrent tool calls: Verify thread safety of git tools

---

## 6. Implementation Timeline

### 6.1 Detailed Schedule

**Week 1: Foundation (Days 1-2)**

**Day 1: Phase 1 - Expose Filesystem Tools**
- Morning:
  - [ ] Modify `developer/developer.py`: Add `builtin_tools=["filesystem", "terminal"]`
  - [ ] Modify `aworld_agent.py`: Create sandbox with builtin_tools
  - [ ] Update `mcp_servers` list to include "filesystem"
- Afternoon:
  - [ ] Test filesystem tools availability
  - [ ] Verify read_file, write_file, list_directory work
  - [ ] Write unit tests for tool exposure
  - [ ] Document changes in code comments

**Day 2: Phase 2 Start - Glob Tool**
- Morning:
  - [ ] Create `aworld/tools/glob_tool.py`
  - [ ] Implement glob_search function with @be_tool
  - [ ] Add error handling and path validation
- Afternoon:
  - [ ] Register glob tool in agent tool_names
  - [ ] Write unit tests for glob tool
  - [ ] Test glob tool via agent
  - [ ] Document glob tool usage

**Week 1: Core Tools (Days 3-4)**

**Day 3: Git Tools (Part 1)**
- Morning:
  - [ ] Create `aworld/tools/git_tools.py`
  - [ ] Implement git_status function
  - [ ] Implement git_diff function
- Afternoon:
  - [ ] Write unit tests for git_status and git_diff
  - [ ] Test git tools in various scenarios (git repo, non-repo, etc.)
  - [ ] Document git tools

**Day 4: Git Tools (Part 2)**
- Morning:
  - [ ] Implement git_log function
  - [ ] Implement git_commit function
  - [ ] Implement git_blame function
- Afternoon:
  - [ ] Write unit tests for git_log, git_commit, git_blame
  - [ ] Register all git tools in agent tool_names
  - [ ] Integration test: agent using git tools
  - [ ] Document all git tools

**Week 2: Commands (Days 5-7)**

**Day 5: Command Framework**
- Morning:
  - [ ] Create `aworld-cli/src/aworld_cli/core/command_system.py`
  - [ ] Implement Command base class
  - [ ] Implement CommandRegistry
  - [ ] Implement register_command decorator
- Afternoon:
  - [ ] Modify CLI REPL to detect "/" prefix
  - [ ] Implement command routing in handle_user_input
  - [ ] Implement inject_command_permissions helper
  - [ ] Write unit tests for command framework

**Day 6: Core Commands**
- Morning:
  - [ ] Create `aworld-cli/src/aworld_cli/commands/commit.py`
  - [ ] Implement CommitCommand class
  - [ ] Test /commit command manually
- Afternoon:
  - [ ] Create `aworld-cli/src/aworld_cli/commands/review.py`
  - [ ] Create `aworld-cli/src/aworld_cli/commands/diff.py`
  - [ ] Implement ReviewCommand and DiffCommand classes
  - [ ] Test all three commands manually

**Day 7: Testing & Polish**
- Morning:
  - [ ] Write integration tests for all commands
  - [ ] Test error handling (non-git repos, etc.)
  - [ ] Performance testing
- Afternoon:
  - [ ] Code review and cleanup
  - [ ] Update documentation
  - [ ] Create PR with all changes
  - [ ] Celebrate! 🎉

### 6.2 Milestones

| Milestone | Date | Deliverables |
|-----------|------|--------------|
| **M1: Tools Exposed** | End of Day 1 | Filesystem tools available to agents |
| **M2: Core Tools Added** | End of Day 4 | Glob + 5 Git tools functional |
| **M3: Commands Working** | End of Day 7 | /commit, /review, /diff commands operational |
| **M4: Production Ready** | End of Week 2 | All tests passing, documentation complete |

---

## 7. Success Criteria

### 7.1 Functional Requirements

**Must Have (Phase 1):**
- [x] Filesystem tools visible to agent
- [x] Agent can read, write, edit, list, search files via tools
- [x] No regression in existing functionality
- [x] Zero sandbox code changes

**Must Have (Phase 2):**
- [x] Glob tool finds files by pattern
- [x] Git tools return structured output
- [x] All 6 new tools (glob + 5 git) callable by agent
- [x] Error handling for edge cases (non-git repo, etc.)

**Must Have (Phase 3):**
- [x] /commit, /review, /diff commands functional
- [x] Commands generate contextual prompts
- [x] Commands auto-allow their tool whitelist
- [x] /help command shows all available commands
- [x] Command framework extensible for future additions

**Nice to Have (Future):**
- [ ] /mcp command to manage MCP servers
- [ ] /skills command to list and activate skills
- [ ] /config command to modify agent configuration
- [ ] Rich terminal UI improvements (progress bars, colors)
- [ ] Command history and autocomplete

### 7.2 Non-Functional Requirements

**Performance:**
- Tool call latency: < 100ms for local tools
- Command execution: < 10 seconds for /commit
- Memory: No leaks from repeated tool calls
- Startup time: No significant regression (< 500ms added)

**Reliability:**
- Error rate: < 1% for valid inputs
- Graceful degradation: Commands fail with helpful errors
- No crashes from malformed inputs

**Usability:**
- Commands discoverable via /help
- Error messages actionable and clear
- Tool outputs LLM-friendly (structured, parseable)

**Maintainability:**
- Code coverage: > 80% for new code
- Documentation: All public APIs documented
- No increase in technical debt

### 7.3 Acceptance Criteria

**Phase 1 Acceptance:**
```bash
$ aworld-cli
> List all files in the current directory
Agent: [calls filesystem:list_directory]
[FILE] README.md
[FILE] setup.py
[DIR] src
...
✓ Pass
```

**Phase 2 Acceptance:**
```bash
> Find all Python test files
Agent: [calls glob("test_*.py")]
test_agent.py
test_tools.py
test_commands.py
✓ Pass

> What's the current git status?
Agent: [calls git_status()]
On branch: main
Modified files (2):
  src/agent.py
  tests/test_agent.py
✓ Pass
```

**Phase 3 Acceptance:**
```bash
> /commit
Executing /commit...
[Agent analyzes changes]
[Agent creates commit]
✓ Commit created: a3f2b1c

feat: add filesystem tools support

Updated developer and aworld agents to expose builtin filesystem tools.
✓ Pass

> /help
Available commands:
  /commit         Create a git commit with intelligent message generation
  /review         Perform code review on current changes
  /diff           Show and summarize code changes
✓ Pass
```

---

## 8. Appendix

### 8.1 References

**Claude Code Source Code:**
- Tool system: `/Users/wuman/Documents/workspace/claudecode/src/tools/`
- Command system: `/Users/wuman/Documents/workspace/claudecode/src/commands/`
- Key patterns: `buildTool()`, prompt-based commands, permission injection

**AWorld Existing Code:**
- Sandbox: `aworld/sandbox/`
- Tools: `aworld/tools/`, `aworld/core/tool/`
- Agents: `aworld-cli/src/aworld_cli/inner_plugins/smllc/agents/`

**Design Documents:**
- CLAUDE.md: Project conventions and principles
- Benchmark-Driven Development: `CLAUDE.md` section on BDD methodology

### 8.2 Code Structure Summary

**New Files:**
```
aworld/tools/
├── glob_tool.py                 # Glob pattern matching tool
└── git_tools.py                 # 5 git tools (status, diff, log, commit, blame)

aworld-cli/src/aworld_cli/core/
└── command_system.py            # Command framework (Command, CommandRegistry)

aworld-cli/src/aworld_cli/commands/
├── __init__.py
├── commit.py                    # /commit command
├── review.py                    # /review command
└── diff.py                      # /diff command

tests/
├── tools/
│   ├── test_glob_tool.py
│   └── test_git_tools.py
├── commands/
│   ├── test_command_system.py
│   ├── test_commit_command.py
│   ├── test_review_command.py
│   └── test_diff_command.py
└── integration/
    └── test_agent_with_new_tools.py
```

**Modified Files:**
```
aworld-cli/src/aworld_cli/inner_plugins/smllc/agents/
├── developer/developer.py       # Add builtin_tools to sandbox
└── aworld_agent.py              # Add builtin_tools + new tool registrations

aworld-cli/src/aworld_cli/main.py  # Add command detection and routing
```

### 8.3 Configuration Examples

**Enable Filesystem Tools:**
```python
# In any agent file
sandbox = Sandbox(
    mcp_config={...},
    builtin_tools=["filesystem", "terminal"]  # ✅ Enable builtin tools
)

agent = Agent(
    ...
    mcp_servers=["terminal", "filesystem"],  # Include filesystem
    sandbox=sandbox,
    tool_names=[..., "glob", "git_status", ...]  # Register new tools
)
```

**Register Custom Command:**
```python
from aworld_cli.core.command_system import Command, register_command

@register_command
class MyCommand(Command):
    @property
    def name(self) -> str:
        return "my_command"
    
    @property
    def allowed_tools(self) -> List[str]:
        return ["terminal:*"]
    
    async def get_prompt(self, context: CommandContext) -> str:
        return "Your prompt here..."
```

### 8.4 Glossary

- **Builtin Tools**: Pre-configured MCP servers (filesystem, terminal) that can be auto-enabled via `builtin_tools` parameter
- **Slash Command**: CLI shortcut starting with `/` that generates a contextual prompt for the agent
- **Prompt-Based Command**: Command that generates dynamic prompts rather than executing hardcoded logic
- **@be_tool Decorator**: AWorld's function-to-tool converter that wraps a Python function as an agent tool
- **MCP (Model Context Protocol)**: Standardized protocol for tool integration in LLM applications
- **CAST**: Code Abstract Syntax Tree - AWorld's advanced code analysis and modification system
- **TeamSwarm**: AWorld's multi-agent orchestration pattern with leader-follower structure

---

## 9. Conclusion

This design document outlines a comprehensive, three-phase enhancement plan to elevate the AWorld agent to Claude Code-level capabilities while maintaining AWorld's harness-first philosophy.

**Key Principles:**
- **Exposure over creation**: Reveal hidden filesystem tools before building new ones
- **Prompt-based commands**: Follow Claude Code's pattern for flexibility and maintainability
- **Progressive enhancement**: Each phase delivers immediate value
- **Zero breaking changes**: All enhancements are additive and backward-compatible

**Expected Impact:**
- **Phase 1** (Day 1): +13 filesystem tools → immediate file operation capabilities
- **Phase 2** (Days 2-4): +6 core tools (glob + git) → complete version control workflow
- **Phase 3** (Days 5-7): +3 slash commands → 10x faster high-frequency operations

**Next Steps:**
1. Review and approve this design document
2. Create feature branch: `feat/aworld-agent-enhancement`
3. Begin Phase 1 implementation
4. Iterate based on testing and feedback
5. Deploy incrementally with benchmark validation

**Success Metric**: AWorld agent can perform all common development tasks (file ops, git workflow, code review) with efficiency comparable to Claude Code, while maintaining superior code intelligence (CAST) and multi-agent coordination (TeamSwarm).

---

**Document Status**: Ready for Review  
**Last Updated**: 2026-04-02  
**Next Review**: After Phase 1 completion
