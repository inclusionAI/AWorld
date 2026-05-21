# CLI Hook Acceptance Example Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone `examples/cli_hook_acceptance/` package that demonstrates hook behavior through manual `aworld-cli` runs with a local AWorld agent.

**Architecture:** The example stays self-contained under `examples/cli_hook_acceptance/`. A single local agent file provides predictable cleanup-oriented behavior, `.aworld/hooks.yaml` wires four shell hooks into the existing hook runtime, and `README.md` gives copy-paste commands plus fixed demo prompts so a user can manually observe each hook stage.

**Tech Stack:** Python, `aworld-cli`, existing AWorld hook runtime, YAML hook config, shell hook scripts, Markdown documentation

---

## File Structure

**Create:**
- `examples/cli_hook_acceptance/README.md`
- `examples/cli_hook_acceptance/agent.py`
- `examples/cli_hook_acceptance/.aworld/hooks.yaml`
- `examples/cli_hook_acceptance/hooks/block_user_input.sh`
- `examples/cli_hook_acceptance/hooks/rewrite_user_input.sh`
- `examples/cli_hook_acceptance/hooks/block_rm_rf.sh`
- `examples/cli_hook_acceptance/hooks/audit_tool_output.sh`

**Modify:**
- None expected for the happy path. If the example reveals a framework gap, stop and add a focused follow-up task instead of broadening scope silently.

**Manual Verification Targets:**
- `examples/cli_hook_acceptance/README.md`
- `examples/cli_hook_acceptance/agent.py`
- `examples/cli_hook_acceptance/.aworld/hooks.yaml`

### Task 1: Scaffold The Acceptance Example Directory

**Files:**
- Create: `examples/cli_hook_acceptance/agent.py`

- [ ] **Step 1: Create the example directory and confirm it does not already exist**

Run:

```bash
test ! -e examples/cli_hook_acceptance && mkdir -p examples/cli_hook_acceptance/hooks
```

Expected:
- command exits `0`
- `examples/cli_hook_acceptance/` now exists

- [ ] **Step 2: Add the demo agent file**

Write `examples/cli_hook_acceptance/agent.py`:

```python
from aworld_cli.core import agent
from aworld.core.agent.swarm import Swarm
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
import os


@agent(
    name="CliHookAcceptanceAgent",
    desc="Demo AWorld agent for manual aworld-cli hook acceptance walkthroughs",
)
def build_cli_hook_acceptance_swarm():
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.2")),
        )
    )

    demo_agent = Agent(
        name="cli_hook_demo_agent",
        desc=(
            "A narrow demo agent for cleanup-oriented tasks. Prefer explicit, safe shell "
            "commands scoped to the current example workspace. Never use rm -rf unless the "
            "user explicitly requests destructive cleanup."
        ),
        conf=agent_config,
    )

    return Swarm(demo_agent)
```

- [ ] **Step 3: Run a syntax smoke check on the new agent**

Run:

```bash
python -m py_compile examples/cli_hook_acceptance/agent.py
```

Expected:
- command exits `0`
- no syntax errors printed

- [ ] **Step 4: Commit the scaffold**

```bash
git add examples/cli_hook_acceptance/agent.py
git commit -m "Add CLI hook acceptance demo agent"
```

### Task 2: Add Hook Scripts And Hook Configuration

**Files:**
- Create: `examples/cli_hook_acceptance/.aworld/hooks.yaml`
- Create: `examples/cli_hook_acceptance/hooks/block_user_input.sh`
- Create: `examples/cli_hook_acceptance/hooks/rewrite_user_input.sh`
- Create: `examples/cli_hook_acceptance/hooks/block_rm_rf.sh`
- Create: `examples/cli_hook_acceptance/hooks/audit_tool_output.sh`

- [ ] **Step 1: Add the `user_input_received` deny hook**

Write `examples/cli_hook_acceptance/hooks/block_user_input.sh`:

```bash
#!/bin/bash

if echo "$AWORLD_MESSAGE_JSON" | grep -Eiq 'rm -rf|delete everything|wipe the workspace'; then
cat <<'EOF'
{
  "continue": true,
  "permission_decision": "deny",
  "permission_decision_reason": "Destructive prompt blocked before agent execution"
}
EOF
else
cat <<'EOF'
{
  "continue": true
}
EOF
fi
```

- [ ] **Step 2: Add the `user_input_received` rewrite hook**

Write `examples/cli_hook_acceptance/hooks/rewrite_user_input.sh`:

```bash
#!/bin/bash

if echo "$AWORLD_MESSAGE_JSON" | grep -Fq 'clean up build artifacts'; then
cat <<'EOF'
{
  "continue": true,
  "updated_input": {
    "content": "List the files under ./tmp/build first, then remove only build artifacts under ./tmp/build using the safest shell command you can."
  }
}
EOF
else
cat <<'EOF'
{
  "continue": true
}
EOF
fi
```

- [ ] **Step 3: Add the `before_tool_call` destructive command blocker**

Write `examples/cli_hook_acceptance/hooks/block_rm_rf.sh`:

```bash
#!/bin/bash

if echo "$AWORLD_MESSAGE_JSON" | grep -Fq 'rm -rf'; then
cat <<'EOF'
{
  "continue": true,
  "permission_decision": "deny",
  "permission_decision_reason": "Destructive command blocked by before_tool_call hook"
}
EOF
else
cat <<'EOF'
{
  "continue": true
}
EOF
fi
```

- [ ] **Step 4: Add the `after_tool_call` audit hook**

Write `examples/cli_hook_acceptance/hooks/audit_tool_output.sh`:

```bash
#!/bin/bash

cat <<'EOF'
{
  "continue": true,
  "system_message": "Audit hook observed a safe tool call.",
  "updated_output": {
    "info": {
      "audit_logged": true,
      "audit_source": "cli_hook_acceptance"
    }
  }
}
EOF
```

- [ ] **Step 5: Make the hook scripts executable**

Run:

```bash
chmod 755 \
  examples/cli_hook_acceptance/hooks/block_user_input.sh \
  examples/cli_hook_acceptance/hooks/rewrite_user_input.sh \
  examples/cli_hook_acceptance/hooks/block_rm_rf.sh \
  examples/cli_hook_acceptance/hooks/audit_tool_output.sh
```

Expected:
- command exits `0`

- [ ] **Step 6: Add the shared hook config**

Write `examples/cli_hook_acceptance/.aworld/hooks.yaml`:

```yaml
version: "2"
hooks:
  user_input_received:
    - name: block-user-input
      type: command
      enabled: true
      command: ./hooks/block_user_input.sh
    - name: rewrite-user-input
      type: command
      enabled: true
      command: ./hooks/rewrite_user_input.sh

  before_tool_call:
    - name: block-rm-rf
      type: command
      enabled: true
      command: ./hooks/block_rm_rf.sh

  after_tool_call:
    - name: audit-tool-output
      type: command
      enabled: true
      command: ./hooks/audit_tool_output.sh
```

- [ ] **Step 7: Smoke-check the hook config parses as YAML**

Run:

```bash
python - <<'PY'
import yaml
from pathlib import Path
path = Path("examples/cli_hook_acceptance/.aworld/hooks.yaml")
data = yaml.safe_load(path.read_text())
assert "hooks" in data
assert "user_input_received" in data["hooks"]
assert "before_tool_call" in data["hooks"]
assert "after_tool_call" in data["hooks"]
print("ok")
PY
```

Expected:
- output includes `ok`

- [ ] **Step 8: Commit the hook package**

```bash
git add \
  examples/cli_hook_acceptance/.aworld/hooks.yaml \
  examples/cli_hook_acceptance/hooks/block_user_input.sh \
  examples/cli_hook_acceptance/hooks/rewrite_user_input.sh \
  examples/cli_hook_acceptance/hooks/block_rm_rf.sh \
  examples/cli_hook_acceptance/hooks/audit_tool_output.sh
git commit -m "Add CLI hook acceptance demo hooks"
```

### Task 3: Write The Manual Acceptance Walkthrough

**Files:**
- Create: `examples/cli_hook_acceptance/README.md`

- [ ] **Step 1: Add the walkthrough README**

Write `examples/cli_hook_acceptance/README.md`:

```md
# CLI Hook Acceptance Demo

This example demonstrates AWorld hook behavior through manual `aworld-cli` usage with a local AWorld agent.

## Preconditions

- Run from the repository root.
- `aworld-cli` is installed in your environment.
- Your normal LLM environment variables are already configured.

## Start The Demo

```bash
cd examples/cli_hook_acceptance
mkdir -p tmp/build
printf 'artifact\n' > tmp/build/demo.txt
aworld-cli --agent-file ./agent.py --agent CliHookAcceptanceAgent
```

## Scenario 1: Block A Dangerous Prompt Before Agent Execution

Type:

```text
please run rm -rf /tmp/build immediately
```

Expected outcome:
- `user_input_received` blocks the request before the agent runs
- the CLI prints the deny reason

## Scenario 2: Rewrite A Prompt Before It Reaches The Agent

Type:

```text
clean up build artifacts
```

Expected outcome:
- the input is rewritten into a safer scoped cleanup instruction
- the agent works on `./tmp/build` instead of an unbounded cleanup request

## Scenario 3: Block A Dangerous Tool Command

Type:

```text
Use a shell command to remove ./tmp/build and do it with rm -rf.
```

Expected outcome:
- the agent attempts a destructive tool call
- `before_tool_call` blocks it with a deny reason

## Scenario 4: Observe Audit Context After A Safe Tool Call

Type:

```text
Use a shell command to list files in ./tmp/build, then remove only ./tmp/build/demo.txt safely.
```

Expected outcome:
- the tool call is allowed
- the post-tool hook adds audit context to the tool result

## Notes

- This package is a manual acceptance demo, not a production security boundary.
- The demo agent is intentionally narrow so the listed prompts are easier to reproduce.
- If your model chooses a different tool path, restart the session and use the exact prompt text above.
```

- [ ] **Step 2: Run a Markdown sanity check by reading the rendered content locally**

Run:

```bash
sed -n '1,260p' examples/cli_hook_acceptance/README.md
```

Expected:
- commands and prompts are copy-paste ready
- every scenario has one exact query and one expected outcome block

- [ ] **Step 3: Perform the documented manual smoke run**

Run:

```bash
cd examples/cli_hook_acceptance
python -m py_compile agent.py
python - <<'PY'
import yaml
from pathlib import Path
print(yaml.safe_load(Path(".aworld/hooks.yaml").read_text())["hooks"].keys())
PY
```

Expected:
- agent compiles successfully
- hook keys print with `user_input_received`, `before_tool_call`, and `after_tool_call`

- [ ] **Step 4: Commit the walkthrough**

```bash
git add examples/cli_hook_acceptance/README.md
git commit -m "Document CLI hook acceptance demo"
```

### Task 4: Final Validation And Handoff

**Files:**
- Review: `examples/cli_hook_acceptance/README.md`
- Review: `examples/cli_hook_acceptance/agent.py`
- Review: `examples/cli_hook_acceptance/.aworld/hooks.yaml`

- [ ] **Step 1: Review the example directory layout**

Run:

```bash
find examples/cli_hook_acceptance -maxdepth 3 -type f | sort
```

Expected:
- output lists the README, agent, hook config, and four hook scripts

- [ ] **Step 2: Review git diff for only intended files**

Run:

```bash
git diff -- examples/cli_hook_acceptance
```

Expected:
- diff is limited to the new example package

- [ ] **Step 3: Commit the final acceptance package**

```bash
git add examples/cli_hook_acceptance
git commit -m "Add CLI hook acceptance example"
```
