# File Path Display Improvement

## 问题描述

当工具输出较大（>50行）时，内容会被自动保存到文件。但文件路径显示不够明显，嵌入在折叠提示的括号内：

```
… +135 lines (saved to /tmp/aworld_outputs/terminal_bash_123456.txt)
```

用户可能忽略文件路径，不知道完整输出保存在哪里。

## 改进方案

### 显示格式改进

**Before:**
```
… +135 lines (saved to /tmp/aworld_outputs/terminal_bash_123456.txt)
```

**After:**
```
… +135 lines
💾 Full output saved to: /tmp/aworld_outputs/terminal_bash_123456.txt
```

### 设计要点

1. **独立一行** - 文件路径单独占一行，不嵌入其他信息
2. **视觉图标** - 使用 💾 图标提高辨识度
3. **颜色高亮** - 
   - Cyan: 标签文字 "Full output saved to:"
   - Green: 文件路径
   - Yellow: 命令建议（在 history 中）
4. **一致性** - 在所有显示位置保持相同格式

## 修改文件

### 1. output_manager.py

**File:** `aworld-cli/src/aworld_cli/executors/output_manager.py`

**Change @ lines 94-99:**
```python
# Before
if file_path:
    display_lines.append(f"     [dim]… +{remaining} lines (saved to {file_path})[/dim]")
else:
    display_lines.append(f"     [dim]… +{remaining} lines[/dim]")

# After
display_lines.append(f"     [dim]… +{remaining} lines[/dim]")

# Add file path on separate line if saved
if file_path:
    display_lines.append(f"     [cyan]💾 Full output saved to:[/cyan] [green]{file_path}[/green]")
```

### 2. base_executor.py

**File:** `aworld-cli/src/aworld_cli/executors/base_executor.py`

**Change @ lines 1274-1282:**
```python
# Before
if total_lines > 50:
    output_mgr = OutputManager()
    save_path = output_mgr._save_output(tool_info, display_content)
    self.console.print(f"     [dim]… +{remaining} lines (saved to {save_path})[/dim]")
else:
    self.console.print(f"     [dim]… +{remaining} lines[/dim]")

# After
if total_lines > 50:
    output_mgr = OutputManager()
    save_path = output_mgr._save_output(tool_info, display_content)
    self.console.print(f"     [dim]… +{remaining} lines[/dim]")
    self.console.print(f"     [cyan]💾 Full output saved to:[/cyan] [green]{save_path}[/green]")
else:
    self.console.print(f"     [dim]… +{remaining} lines[/dim]")
```

### 3. history.py (history command)

**File:** `aworld-cli/src/aworld_cli/commands/history.py`

**Change @ lines 212-219:**
```python
# Before
if target_call.get('metadata', {}).get('output_file'):
    output_file = target_call['metadata']['output_file']
    lines.extend([
        f"[dim]Full output saved to:[/dim]",
        f"  {output_file}",
        "",
        f"[dim]View with:[/dim] cat {output_file}"
    ])

# After
if target_call.get('metadata', {}).get('output_file'):
    output_file = target_call['metadata']['output_file']
    lines.extend([
        "",
        f"[cyan]💾 Full output saved to:[/cyan]",
        f"  [green]{output_file}[/green]",
        "",
        f"[dim]View with:[/dim] [yellow]cat {output_file}[/yellow]"
    ])
```

## 显示效果

### 1. 工具输出显示

```
⏺ terminal → bash
  ⎿  Line 0: Some content here...
     Line 1: Some content here...
     ...
     Line 14: Some content here...
     … +135 lines
     💾 Full output saved to: /tmp/aworld_outputs/terminal_bash_20260403.txt
```

### 2. History 命令显示

```
Tool Call #5

Tool: terminal → bash
Time: 2026-04-03T13:33:00.000Z
Duration: 2.345s
Status: success

💾 Full output saved to:
  /Users/wuman/.aworld/tool_calls/outputs/terminal_bash_1234567890.txt

View with: cat /Users/wuman/.aworld/tool_calls/outputs/terminal_bash_1234567890.txt
```

## 用户体验改进

### Before
- ❌ 文件路径嵌入在灰色文字中，不易发现
- ❌ 括号内的信息容易被忽略
- ❌ 没有视觉提示

### After
- ✅ 文件路径单独一行，清晰可见
- ✅ 💾 图标增加辨识度
- ✅ 颜色高亮（cyan + green）吸引注意力
- ✅ 在所有相关位置保持一致

## 测试验证

运行测试脚本验证改进：

```bash
python test_output_display.py
```

Expected output:
```
✓ File paths now displayed on separate line
✓ Using 💾 icon for visibility
✓ Cyan color for label, green for path
✓ Commands highlighted in yellow
✓ Consistent across output_manager, base_executor, and history command
```

## 设计原则

1. **可见性 > 简洁性** - 重要信息（文件路径）应该明显，即使多占一行
2. **一致性** - 同类信息在不同位置使用相同格式
3. **视觉层次** - 使用颜色和图标建立清晰的视觉层次
4. **行为提示** - 提供明确的操作建议（如 "View with: cat ..."）

## 相关文档

- [Tool Output UX Improvements](./tool-output-ux-improvements.md)
- [Tool Call Logging System](./tool-call-logging.md)

---
Date: 2026-04-03
Issue: User feedback - file paths not visible enough
Resolution: Separate line display with icon and color highlighting
