# Tool Call Logging System

## Overview

AWorld now includes comprehensive tool call logging for debugging and AI-assisted problem diagnosis. All tool calls are automatically logged with rich context, making it easy to:

1. **Debug issues** - Trace what happened when things go wrong
2. **Let AI help** - Models can read logs to diagnose problems
3. **Track performance** - See which tools are slow or failing

## Features

### 1. **Automatic Logging**
Every tool call is automatically logged with:
- Tool name and arguments
- Full output (large outputs saved to separate files)
- Execution time
- Success/failure status
- Error messages and stack traces
- Execution context

### 2. **AI-Readable Format**
Logs are structured JSON with rich context:
```json
{
  "_comment": "AWorld tool call log - AI/human readable",
  "_schema_version": "1.0",
  "timestamp": "2026-04-03T13:30:00.123Z",
  "tool_name": "terminal → bash",
  "args": {
    "command": "git status --short"
  },
  "output": "M  aworld-cli/src/aworld_cli/console.py\nA  aworld-cli/src/aworld_cli/commands/__init__.py...",
  "output_stats": {
    "lines": 35,
    "chars": 1842,
    "truncated": true
  },
  "duration_seconds": 0.234,
  "status": "success",
  "metadata": {
    "tool_call_id": "call_abc123",
    "summary": "35 files changed"
  },
  "context": {
    "session_id": "session_20260403_a1b2c3d4"
  }
}
```

### 3. **Smart Output Handling**
- **Small outputs** (<1000 chars): Stored inline in log
- **Large outputs** (>1000 chars): Saved to separate file with reference
- Output files include header comments for context

### 4. **Search and Retrieval**
Use `/history` command to search logs:
```bash
/history              # Show recent 10 calls
/history 20           # Show recent 20 calls
/history bash         # Filter by tool name
/history --failed     # Show only failures
/history --full 5     # Show full output of call #5
```

## Log Locations

### Session Logs
```
~/.aworld/tool_calls/<session_id>.jsonl
```
One file per session, containing all tool calls in that session.

### Large Outputs
```
~/.aworld/tool_calls/outputs/<tool_name>_<timestamp>.txt
```
Separate files for outputs >1000 characters.

### Latest Session
```
~/.aworld/tool_calls/latest -> <session_id>.jsonl
```
Symlink to current session for quick access.

## Log Format

### Session Start
```json
{
  "_type": "session_start",
  "_comment": "AWorld tool call session log - AI/human readable",
  "session_id": "session_20260403_a1b2c3d4",
  "timestamp": "2026-04-03T13:30:00.000Z",
  "metadata": {
    "agent_name": "Aworld",
    "project": "/path/to/project",
    "platform": "darwin",
    "python_version": "3.11.5"
  },
  "format_version": "1.0"
}
```

### Tool Call
```json
{
  "_call_number": 1,
  "timestamp": "2026-04-03T13:30:01.234Z",
  "tool_name": "terminal → bash",
  "args": {
    "command": "ls -la | head -20"
  },
  "output": "total 296\ndrwxr-xr-x   9 user  staff   288 ...",
  "output_stats": {
    "lines": 20,
    "chars": 850,
    "truncated": false
  },
  "duration_seconds": 0.123,
  "status": "success"
}
```

### Tool Call with Error
```json
{
  "_call_number": 2,
  "timestamp": "2026-04-03T13:30:02.456Z",
  "tool_name": "terminal → bash",
  "args": {
    "command": "invalid_command"
  },
  "output": "",
  "output_stats": {
    "lines": 0,
    "chars": 0,
    "truncated": false
  },
  "duration_seconds": 0.050,
  "status": "error",
  "error": "Command not found: invalid_command",
  "metadata": {
    "error_traceback": "Traceback (most recent call last):\n  ..."
  }
}
```

### Session End
```json
{
  "_type": "session_end",
  "session_id": "session_20260403_a1b2c3d4",
  "timestamp": "2026-04-03T13:35:00.000Z",
  "total_calls": 15,
  "metadata": {
    "duration_seconds": 300
  }
}
```

## Usage Examples

### 1. View Recent Tool Calls
```bash
> /history

Recent 10 Tool Calls
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   Time             Tool                   Status  Duration  Output
1   2026-04-03 13:30 terminal → bash        ✓       0.12s     M  aworld-cli/src/...
2   2026-04-03 13:31 CAST_SEARCH → grep...  ✓       0.45s     search_type: grep...
3   2026-04-03 13:32 terminal → git_status  ✓       0.08s     35 files changed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Tip: Use /history --full <#> to see full output
     Use /history --failed to see errors
     Use /history bash 20 to filter by tool name
```

### 2. Debug Failed Calls
```bash
> /history --failed

Recent Failed Calls
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   Time             Tool            Status  Duration  Error
5   2026-04-03 13:33 terminal → bash ✗       0.05s     Command not found
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 3. View Full Output
```bash
> /history --full 5

Tool Call #5

Tool: terminal → bash
Time: 2026-04-03T13:33:00.000Z
Duration: 0.050s
Status: error

Arguments:
  command: invalid_command

Error:
Command not found: invalid_command
```

### 4. Filter by Tool Name
```bash
> /history bash 20

Recent 20 Tool Calls (filter: bash)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   Time             Tool              Status  Duration  Output
1   2026-04-03 13:30 terminal → bash   ✓       0.12s     M  aworld-cli/...
3   2026-04-03 13:32 terminal → bash   ✓       0.08s     35 files changed
5   2026-04-03 13:33 terminal → bash   ✗       0.05s     Command not found
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## AI Diagnosis

The log format is designed for AI models to read and diagnose issues:

### Example: Ask AI to Debug
```bash
> I'm getting an error with git status. Can you check the logs?

AI: Let me read the recent tool calls...
[AI reads ~/.aworld/tool_calls/latest]

AI: I see the issue. Call #5 failed because the command was 
"git statsu" (typo). The correct command is "git status".
The error message shows: "git: 'statsu' is not a git command"
```

### Example: Performance Analysis
```bash
> Why is the CAST search taking so long?

AI: Let me analyze the tool call logs...
[AI reads recent CAST_SEARCH calls]

AI: I found the issue. Your CAST_SEARCH calls are taking 3-5 seconds
because you're searching the entire project directory including
node_modules. Try adding --exclude "node_modules" to your searches.
```

## Log Retention

- **Session logs**: Kept indefinitely (user can clean up manually)
- **Output files**: Kept indefinitely (user can clean up manually)
- **Typical size**: ~100KB per session (varies by tool usage)

### Manual Cleanup
```bash
# Remove old sessions (older than 30 days)
find ~/.aworld/tool_calls -name "*.jsonl" -mtime +30 -delete

# Remove large output files (older than 7 days)
find ~/.aworld/tool_calls/outputs -type f -mtime +7 -delete
```

## Implementation Details

### ToolLogger Class
- **Singleton pattern**: One global logger instance
- **Thread-safe**: Safe for concurrent tool calls
- **Non-blocking**: Logging failures don't break execution
- **Performance**: <1ms overhead per tool call

### Integration Points
1. **BaseAgentExecutor**: Auto-starts logging in `__init__`
2. **Tool rendering**: Logs after `_render_simple_tool_result_output`
3. **Session management**: New log file per session

### Error Handling
- **Log failures**: Logged to debug logger, don't break execution
- **File I/O errors**: Gracefully skipped
- **JSON errors**: Logged as debug messages

## Benefits for Development

### 1. **Faster Debugging**
Instead of:
- ❌ "What command did I run?"
- ❌ "What was the output?"
- ❌ "When did it fail?"

You get:
- ✅ Complete history of all commands
- ✅ Exact outputs preserved
- ✅ Precise timestamps and durations

### 2. **AI-Assisted Diagnosis**
- Model can read logs to understand context
- Identify patterns in failures
- Suggest fixes based on error history

### 3. **Performance Tracking**
- See which tools are slow
- Identify performance regressions
- Optimize hot paths

### 4. **Audit Trail**
- Complete record of agent actions
- Useful for reproducing issues
- Understand agent decision-making

## Future Enhancements

Planned features:
1. **Interactive history viewer** - TUI for browsing logs
2. **Log analytics** - Tool usage stats and trends
3. **Automatic issue detection** - Detect patterns in failures
4. **Log export** - Export logs in various formats
5. **Integration with observability tools** - Send logs to external systems

## Comparison with Claude Code

| Feature | Claude Code | AWorld (New) | Status |
|---------|-------------|--------------|--------|
| Session logs | `~/.claude/debug/` | `~/.aworld/tool_calls/` | ✅ |
| Tool call tracking | Yes | Yes | ✅ |
| AI-readable format | Yes | Yes | ✅ |
| Search/filter | Via IDE | `/history` command | ✅ |
| Large output handling | Inline | Separate files | ✅+ |
| Error tracking | Basic | Full stack traces | ✅+ |
| Performance metrics | No | Yes (duration) | ✅+ |

## Summary

The tool call logging system provides:
- 📝 **Complete history** of all tool calls
- 🔍 **Easy search** with `/history` command
- 🤖 **AI-readable** format for diagnosis
- 📊 **Performance tracking** with durations
- 🐛 **Better debugging** with full context

All logging is **automatic** and **non-intrusive** - just use AWorld as normal, and logs are there when you need them.
