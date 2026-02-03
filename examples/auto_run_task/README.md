# Auto Run Task Examples

This directory contains examples demonstrating the **Auto Run Task** feature in AWorld, which enables automatic task planning and execution using MetaAgent.

## Overview

The Auto Run Task feature provides three main interfaces:

1. **`auto_run_task`**: One-step planning + execution (convenient)
2. **`plan_task`**: Generate Task YAML (step 1)
3. **`execute_plan`**: Execute Task from YAML (step 2)

## Examples

### 1. Basic Usage (`example_basic.py`)

**Simplest usage**: Auto-generate plan and execute immediately.

```python
results, yaml_path = await Runners.auto_run_task(
    query="帮我总结一下 AWorld 框架的核心功能",
    skills_path="./skills",
    save_plan=True
)
```

**Run:**
```bash
cd examples/auto_run_task
python example_basic.py
```

---

### 2. Two-Step Workflow (`example_two_step.py`)

**Plan first, review YAML, then execute.**

```python
# Step 1: Generate plan
yaml_path = await Runners.plan_task(
    query="帮我找到最新一周 BABA 的股价并分析趋势",
    skills_path="./skills",
    output_yaml="./stock_analysis_task.yaml"
)

# Review/modify YAML...

# Step 2: Execute
results = await Runners.execute_plan(
    yaml_path="./stock_analysis_task.yaml",
    skills_path="./skills"
)
```

**Run:**
```bash
python example_two_step.py
```

---

### 3. Custom MetaAgent (`example_custom_meta_agent.py`)

**Use specialized MetaAgent** with custom prompt and retry settings.

```python
meta_agent = MetaAgent(
    system_prompt="You are a specialized financial analysis planner...",
    conf=AgentConfig(...),
    max_yaml_retry=5
)

results, yaml_path = await Runners.auto_run_task(
    query="分析 AAPL 最近的财报数据",
    meta_agent=meta_agent,
    skills_path="./skills"
)
```

**Run:**
```bash
python example_custom_meta_agent.py
```

---

### 4. Predefined Agents (`example_predefined_agents.py`)

**Pass pre-configured agents** for MetaAgent to choose from.

```python
search_agent = Agent(name="SearchAgent", ...)
analyst_agent = Agent(name="AnalystAgent", ...)

results, yaml_path = await Runners.auto_run_task(
    query="搜索并分析最新的 AI 技术趋势",
    available_agents={
        "search_agent": search_agent,
        "analyst_agent": analyst_agent
    },
    skills_path="./skills"
)
```

**Run:**
```bash
python example_predefined_agents.py
```

---

## Environment Variables

Set these in your `.env` file:

```bash
LLM_MODEL_NAME=gpt-4o
LLM_PROVIDER=openai
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.openai.com/v1

# Optional (for some examples)
TAVILY_API_KEY=your_tavily_key
```

---

## Task YAML Output

Generated YAML files are saved to `~/.aworld/tasks/` by default, with format:

```
~/.aworld/tasks/
├── 20260130_143022_a1b2c3d4.yaml
├── 20260130_144511_e5f6g7h8.yaml
└── ...
```

**YAML Structure:**

```yaml
task:
  query: "用户查询"

agents:
  - id: orchestrator
    type: builtin
    config: {...}
  - id: analyst_agent
    type: skill
    skill_name: stock_analysis

swarm:
  type: handoff
  root_agent: orchestrator
  agents:
    - id: orchestrator
      next: [analyst_agent]
```

---

## Key Features

✅ **Automatic Planning**: MetaAgent analyzes query and decides optimal agent topology  
✅ **Flexible Agent Types**: builtin, skill (Agentic Skill), predefined  
✅ **Review Before Execute**: Two-step workflow allows YAML modification  
✅ **Custom MetaAgent**: Specialize planning with custom prompts  
✅ **Error Handling**: Auto-retry on YAML generation failure, clear error messages  

---

## Next Steps

- See [AUTO_RUN_TASK_DESIGN.md](../../AUTO_RUN_TASK_DESIGN.md) for full design documentation
- Explore [skill_agent examples](../skill_agent/) for more on Skills and Agentic Skills
- Check [swarm_yaml examples](../swarm_yaml/) for manual YAML-based swarm definitions

---

**Questions?** Check the [AWorld Documentation](../../README.md) or open an issue.
