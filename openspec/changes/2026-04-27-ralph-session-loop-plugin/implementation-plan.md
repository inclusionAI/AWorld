# Ralph Session Loop Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone phase-1 Ralph plugin for the interactive AWorld CLI that loops within the current session using plugin commands, plugin state, and stop hooks.

**Architecture:** Extend plugin commands so a plugin can contribute Python-backed slash commands in addition to markdown prompt commands. Implement a built-in Ralph plugin with a prompt command for `/ralph-loop`, a tool command for `/cancel-ralph`, task-state hooks for final-answer diagnostics, a stop hook for continuation, and a HUD provider for loop status.

**Tech Stack:** Python, pytest, AWorld CLI plugin framework, plugin state store, command registry, plugin hooks, HUD providers

---

### Task 1: Command Loader Foundation

**Files:**
- Modify: `aworld-cli/src/aworld_cli/core/command_system.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_capabilities/commands.py`
- Test: `tests/plugins/test_plugin_commands.py`

- [ ] **Step 1: Write the failing tests for Python-backed plugin commands and session-aware command context**

Add tests that require:

```python
def test_register_python_backed_plugin_command_from_manifest():
    ...
    command = CommandRegistry.get("ralph-loop")
    assert command is not None
    assert command.command_type == "prompt"

def test_command_context_carries_executor_session_id():
    ...
    context = CommandContext(cwd="/tmp", user_args="", runtime=runtime, session_id="session-1")
    assert context.session_id == "session-1"
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/plugins/test_plugin_commands.py -k "python_backed or session_id" -v`
Expected: FAIL because plugin commands only support markdown prompts and `CommandContext` has no `session_id`.

- [ ] **Step 3: Implement the minimal loader and context support**

Required behavior:

```python
@dataclass
class CommandContext:
    cwd: str
    user_args: str
    sandbox: Optional[Any] = None
    agent_config: Optional[Any] = None
    runtime: Optional[Any] = None
    session_id: Optional[str] = None
```

And in plugin command loading:

```python
if target_suffix == ".py":
    command = build_command(plugin, entrypoint)
else:
    command = PluginPromptCommand(plugin, entrypoint)
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/plugins/test_plugin_commands.py -k "python_backed or session_id" -v`
Expected: PASS

### Task 2: Built-in Ralph Plugin Commands

**Files:**
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/ralph_session_loop/.aworld-plugin/plugin.json`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/ralph_session_loop/__init__.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/ralph_session_loop/commands/ralph_loop.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/ralph_session_loop/commands/cancel_ralph.py`
- Test: `tests/plugins/test_plugin_commands.py`

- [ ] **Step 1: Write the failing tests for `/ralph-loop` and `/cancel-ralph` state behavior**

Add tests that require:

```python
async def test_ralph_loop_command_initializes_session_state(tmp_path):
    ...
    payload = json.loads(state_path.read_text())
    assert payload["active"] is True
    assert payload["iteration"] == 1
    assert payload["verify_commands"] == ["pytest tests/api -q"]

async def test_cancel_ralph_clears_session_state(tmp_path):
    ...
    result = await command.execute(context)
    assert "cancelled" in result.lower()
    assert handle.read() == {}
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/plugins/test_plugin_commands.py -k "ralph_loop_command or cancel_ralph" -v`
Expected: FAIL because the built-in plugin and command modules do not exist yet.

- [ ] **Step 3: Implement the built-in plugin commands with minimal argument parsing**

Required behavior:

```python
state = {
    "active": True,
    "prompt": prompt_text,
    "iteration": 1,
    "max_iterations": max_iterations,
    "completion_promise": completion_promise,
    "verify_commands": verify_commands,
    "started_at": started_at,
    "last_stop_reason": None,
    "last_final_answer_excerpt": None,
}
```

And normalized prompt content must include:

```text
Task:
...

Verification requirements:
...

Completion rule:
...
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/plugins/test_plugin_commands.py -k "ralph_loop_command or cancel_ralph" -v`
Expected: PASS

### Task 3: Ralph Hooks And HUD

**Files:**
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/ralph_session_loop/hooks/task_completed.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/ralph_session_loop/hooks/task_error.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/ralph_session_loop/hooks/task_interrupted.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/ralph_session_loop/hooks/stop.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/ralph_session_loop/hud/status.py`
- Test: `tests/plugins/test_plugin_hooks.py`
- Test: `tests/plugins/test_plugin_hud.py`

- [ ] **Step 1: Write the failing tests for completion detection, max-iteration exit, and HUD lines**

Add tests that require:

```python
async def test_ralph_stop_hook_blocks_and_continues_when_active(...):
    assert result.action == "block_and_continue"
    assert "Task:" in result.follow_up_prompt

async def test_ralph_stop_hook_allows_exit_on_exact_completion_promise(...):
    assert result.action == "allow"

async def test_ralph_stop_hook_allows_exit_when_max_iterations_reached(...):
    assert result.action == "allow"

def test_ralph_hud_renders_active_state(...):
    assert any("Ralph: active" in segment for segment in lines[0].segments + lines[1].segments)
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/plugins/test_plugin_hooks.py tests/plugins/test_plugin_hud.py -k "ralph" -v`
Expected: FAIL because the hook and HUD modules do not exist yet.

- [ ] **Step 3: Implement task-state hooks, stop hook, and HUD provider**

Required behavior:

```python
def handle_event(event, state):
    handle = state["__plugin_state__"]
    ...
    return {"action": "allow"}
```

Stop hook policy:

```python
if not active:
    return {"action": "allow"}
if completion promise matched:
    handle.clear()
    return {"action": "allow"}
if max iterations reached:
    handle.clear()
    return {"action": "allow"}
handle.update({"iteration": next_iteration, ...})
return {"action": "block_and_continue", "follow_up_prompt": normalized_prompt}
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/plugins/test_plugin_hooks.py tests/plugins/test_plugin_hud.py -k "ralph" -v`
Expected: PASS

### Task 4: Runtime Integration

**Files:**
- Test: `tests/plugins/test_plugin_end_to_end.py`
- Modify: `aworld-cli/src/aworld_cli/console.py` if needed for command context/session propagation only

- [ ] **Step 1: Write the failing integration test for command initialization plus stop-hook continuation**

Add a test that:

```python
1. loads the built-in Ralph plugin
2. executes `/ralph-loop ...`
3. simulates a completed task hook update without promise match
4. runs the stop hook
5. asserts `block_and_continue` and iteration increment
```

- [ ] **Step 2: Run the focused integration test to verify it fails**

Run: `pytest tests/plugins/test_plugin_end_to_end.py -k "ralph" -v`
Expected: FAIL until the full plugin surface is wired together.

- [ ] **Step 3: Implement any missing glue and keep scope minimal**

Only fill gaps required to make the built-in plugin work through the existing plugin framework. Do not introduce `RalphRunner` dependencies.

- [ ] **Step 4: Run the focused integration test to verify it passes**

Run: `pytest tests/plugins/test_plugin_end_to_end.py -k "ralph" -v`
Expected: PASS

### Task 5: Final Verification

**Files:**
- Modify: `openspec/changes/2026-04-27-ralph-session-loop-plugin/tasks.md`

- [ ] **Step 1: Mark completed OpenSpec tasks that were actually implemented**

- [ ] **Step 2: Run the complete Ralph-related verification suite**

Run: `pytest tests/plugins/test_plugin_commands.py tests/plugins/test_plugin_hooks.py tests/plugins/test_plugin_hud.py tests/plugins/test_plugin_end_to_end.py -k "ralph or python_backed or session_id" -v`
Expected: PASS

- [ ] **Step 3: Run manifest and runtime regression checks around the touched plugin framework paths**

Run: `pytest tests/plugins/test_plugin_framework_manifest.py tests/plugins/test_plugin_framework_state.py -v`
Expected: PASS
