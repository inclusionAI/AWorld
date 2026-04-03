# Tool Output UX Improvements

## Overview

Added Claude Code-style tool output display with compact formatting, smart folding, and automatic file redirection guidance.

## Visual Improvements

### Before
```
▶ bash
   command: git log --all --oneline
   (200 lines of output dumped directly)
```

### After
```
⏺ bash
  ⎿  3362b028 Merge pull request #837...
     7a9b04ef [media_comprehension]: more specially...
     98b7b1a2 [media_comprehension]: more specially...
     ... (15 more lines)
     … +182 lines (saved to /tmp/aworld_outputs/bash_20260403_115642.txt)
```

## Key Features

### 1. **Compact Visual Hierarchy**
- `⏺` - Tool invocation marker
- `⎿` - Output content prefix (first line)
- Consistent indentation for multi-line output

### 2. **Smart Content Folding**
- Auto-truncate after 15 lines (configurable)
- Show remaining line count: `… +N lines`
- Prevent console flooding

### 3. **Automatic File Saving**
- Large output (>50 lines) auto-saved to `/tmp/aworld_outputs/`
- Display save path in fold indicator
- Timestamped filenames for easy tracking

### 4. **Output Management Guidance**
Agent prompt now includes bash best practices:

**Listing files:**
```bash
# ❌ Bad: dumps everything
ls -la

# ✅ Good: limit output
ls -la | head -20
```

**Search results:**
```bash
# ❌ Bad: shows all matches
grep -r "pattern" .

# ✅ Good: count first
grep -r "pattern" . | wc -l
```

**Git logs:**
```bash
# ❌ Bad: unlimited history
git log --all

# ✅ Good: limit results
git log --oneline -20
```

**Large output:**
```bash
# ✅ Best: redirect to file
find . -name "*.py" > /tmp/found_files.txt && wc -l /tmp/found_files.txt
```

## Implementation Details

### New File: `output_manager.py`
```python
class OutputManager:
    FOLD_LINE_THRESHOLD = 20  # Lines before folding
    SAVE_LINE_THRESHOLD = 100  # Lines before auto-save
    MAX_PREVIEW_LINES = 15  # Preview size
    
    def format_tool_output(self, tool_name, output, save_to_file=False):
        """Format with ⏺ and ⎿ symbols, smart folding"""
        # Implementation...
```

### Modified Files
1. **`base_executor.py`**
   - Updated `_format_tool_result_display_lines()` to use ⎿ symbol
   - Enhanced `_render_simple_tool_result_output()` with auto-save
   - Added fold indicator with remaining line count

2. **`prompt.txt`**
   - Added "Output Management (Best Practices)" section
   - Guidance on head/tail, piping, redirection
   - Examples for common scenarios

## Benefits

### For Users
- **Less noise:** Long output no longer floods console
- **Better readability:** Visual hierarchy with symbols
- **Quick scanning:** Preview shows key info, details in file

### For AI Agents
- **Clearer guidance:** Explicit examples in prompt
- **Smarter decisions:** Agent learns to use pipes and redirection
- **Better UX:** Agent-generated commands produce manageable output

## Configuration

Environment variables (optional):
```bash
# Disable truncation (show all output)
export AWORLD_CLI_STREAM_NO_TRUNCATE=1

# Custom max chars per line
export AWORLD_CLI_TOOL_RESULT_SUMMARY_MAX_CHARS=500

# Custom max preview lines
export AWORLD_CLI_TOOL_RESULT_SUMMARY_MAX_LINES=5
```

## Examples

### Example 1: Git Status
```
⏺ terminal → git_status
  ⎿  M  aworld-cli/src/aworld_cli/console.py
     M  aworld-cli/src/aworld_cli/core/config.py
     A  aworld-cli/src/aworld_cli/commands/__init__.py
     … +32 lines
```

### Example 2: Large Find Result
```
⏺ bash
  ⎿  ./aworld/core/agent/base.py
     ./aworld/core/agent/swarm.py
     ./aworld/agents/llm_agent.py
     … +147 lines (saved to /tmp/aworld_outputs/bash_20260403_120000.txt)
```

### Example 3: JSON Output
```
⏺ terminal → search_content
  ⎿  pattern: "class Command"
     files_searched: 156
     matches: [23 items]
     … (2 more fields)
```

## Future Enhancements

1. **Interactive expand:** Add `ctrl+o` to expand folded output
2. **Syntax highlighting:** Colorize code/JSON in preview
3. **Smart summarization:** LLM-generated summaries for complex output
4. **Output history:** Browse past tool outputs easily

## Testing

Run included test to verify:
```bash
python tests/test_output_manager.py
```

Expected: All formatting and folding tests pass ✓

## Migration Notes

No breaking changes. Existing code continues to work:
- Default behavior: compact display (new)
- With `AWORLD_CLI_STREAM_NO_TRUNCATE=1`: original behavior

Users see immediate UX improvement without configuration.
