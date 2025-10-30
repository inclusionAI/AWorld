# Agent Skills - Core Implementation Logic

## Overview

This document explains the core implementation logic of Agent Skills in AWorld Framework, based on Anthropic's progressive disclosure principle.

## Architecture Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     1. AGENT INITIALIZATION                      │
│                                                                   │
│  Agent(skill_configs={...})                                      │
│    ↓                                                              │
│  context.init_skill_list(skill_configs, namespace)               │
│    ↓                                                              │
│  Auto-activate skills with "active": True                        │
│    ↓                                                              │
│  Sandbox derives MCP servers from skill_configs                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                   2. SYSTEM PROMPT GENERATION                    │
│                                                                   │
│  SkillsNeuron formats skill metadata into prompt:               │
│    <skills_guide>                                                │
│      <skill_guide>                                               │
│        Actions: active_skill, offload_skill                      │
│      </skill_guide>                                              │
│      <skills_info>                                               │
│        <skill id="planning">                                     │
│          <skill_name>Planning</skill_name>                       │
│          <skill_desc>Task planning...</skill_desc>               │
│        </skill>                                                  │
│      </skills_info>                                              │
│    </skills_guide>                                               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    3. SKILL ACTIVATION                           │
│                                                                   │
│  Agent decides to use a skill based on task requirements        │
│    ↓                                                              │
│  Agent calls: active_skill(skill_name="browser")                │
│    ↓                                                              │
│  ContextSkillTool executes:                                      │
│    result = await context.active_skill("browser", namespace)    │
│    ↓                                                              │
│  Returns:                                                         │
│    "skill browser activated, current skills: ['browser']         │
│     <skill_guide>Use for navigating websites...</skill_guide>"  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    4. TOOL FILTERING                             │
│                                                                   │
│  Agent prepares to call tools                                    │
│    ↓                                                              │
│  skill_translate_tools() filters available tools:               │
│                                                                   │
│  IF no skills activated:                                         │
│    → Only non-MCP tools available                                │
│                                                                   │
│  IF skills activated:                                            │
│    → Collect tool_list from each active skill                    │
│    → If tool_list is []: include all tools from that server     │
│    → If tool_list is ["tool1", "tool2"]: only those tools      │
│    → Merge all allowed tools                                     │
│                                                                   │
│  Return: Filtered tool list for agent                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     5. TOOL EXECUTION                            │
│                                                                   │
│  Agent uses tools from activated skill:                          │
│    - navigate("https://example.com")                            │
│    - screenshot("page")                                          │
│    - get_visible_text()                                         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    6. SKILL OFFLOADING                           │
│                                                                   │
│  When task phase completes:                                      │
│    ↓                                                              │
│  Agent calls: offload_skill(skill_name="browser")               │
│    ↓                                                              │
│  ContextSkillTool executes:                                      │
│    result = await context.offload_skill("browser", namespace)   │
│    ↓                                                              │
│  Returns:                                                         │
│    "skill browser offloaded, current skills: []"                │
│    ↓                                                              │
│  Subsequent tool calls exclude browser tools                     │
└─────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. Skill Configuration (`skill_configs`)

Defined at agent initialization:

```python
{
    "skill_name": {
        "name": str,           # Display name
        "desc": str,           # Brief description
        "usage": str,          # Detailed usage guide
        "active": bool,        # Optional: auto-activate
        "tool_list": {
            "server_name": []  # Empty = all tools
            # or
            "server_name": ["tool1", "tool2"]  # Specific tools
        }
    }
}
```

### 2. Context Skill Management (`AmniContext`)

**Initialization**:
```python
async def init_skill_list(self, skill_list: Dict[str, Any], namespace: str):
    self.put(SKILL_LIST_KEY, skill_list, namespace=namespace)
    for skill_name, skill_config in skill_list.items():
        if skill_config.get('active', False):
            await self.active_skill(skill_name, namespace)
```

**Activation**:
```python
async def active_skill(self, skill_name: str, namespace: str) -> str:
    # Validate skill exists
    agent_skills = await self.get_skill_name_list(namespace)
    if skill_name not in agent_skills:
        return "skill not found"
    
    # Add to active list
    activate_skills = await self.get_active_skills(namespace)
    activate_skills.append(skill_name)
    self.put(ACTIVE_SKILLS_KEY, activate_skills, namespace=namespace)
    
    # Return activation message with usage guide
    skill = await self.get_skill_list(namespace=namespace)
    return f"skill {skill_name} activated, current skills: {activate_skills}\n\n<skill_guide>{skill.get('usage', '')}</skill_guide>"
```

**Offloading**:
```python
async def offload_skill(self, skill_name: str, namespace: str) -> str:
    skills = await self.get_active_skills(namespace)
    if not skills or skill_name not in skills:
        return f"skill {skill_name} not found, current skills: {skills}"
    
    skills.remove(skill_name)
    self.put(ACTIVE_SKILLS_KEY, skills, namespace=namespace)
    return f"skill {skill_name} offloaded, current skills: {skills}"
```

### 3. Tool Translation (`skill_translate_tools`)

Located in `aworld/mcp_client/utils.py`:

```python
async def skill_translate_tools(
    skills: List[str] = None,
    skill_configs: Dict[str, Any] = None,
    tools: List[Dict[str, Any]] = None,
    tool_mapping: Dict[str, str] = {}
) -> List[Dict[str, Any]]:
```

**Logic**:

1. **No skills activated** (`skills` is empty):
   - Filter out all MCP tools (tools in `tool_mapping`)
   - Return only non-MCP tools (e.g., internal tools)

2. **Skills activated** (`skills` contains skill names):
   - For each active skill, collect its `tool_list`
   - Build a filter map: `{server_name: set(tool_names) | None}`
   - `None` means "all tools from this server"
   - If any skill requests all tools (`[]`), override to `None`
   - Filter `tools` list to only include allowed tools
   - Return filtered tool list

3. **Tool matching**:
   - Match by `tool["function"]["name"]` against allowed tools
   - Match by `tool_mapping[tool_name]` against allowed servers

### 4. Skills Neuron (`SkillsNeuron`)

Located in `aworld/core/context/amni/prompt/neurons/skill_neuron.py`:

```python
@neuron_factory.register(name="skills", desc="skills neuron", prio=2)
class SkillsNeuron(Neuron):
    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        # Get available skills from context
        active_skills = await context.get_skill_list(namespace)
        
        # Format each skill into XML structure
        items = []
        for skill_id, skill in active_skills.items():
            items.append(
                f"  <skill id=\"{skill_id}\">\n"
                f"    <skill_name>{skill['name']}</skill_name>\n"
                f"    <skill_desc>{skill['desc']}</skill_desc>\n"
                f"  </skill>"
            )
        return items
    
    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        # Wrap skills in guide structure
        return SKILLS_PROMPT.format(skills="\n".join(items))
```

### 5. Context Skill Tool (`ContextSkillTool`)

Located in `aworld/core/context/amni/tool/context_skill_tool.py`:

Provides two tool actions:
- `ACTIVE_SKILL`: Activates a skill
- `OFFLOAD_SKILL`: Offloads a skill

When agent calls these tools, they invoke the corresponding `AmniContext` methods.

### 6. Sandbox Integration

Located in `aworld/core/agent/base.py`:

```python
def __init__(self, ..., skill_configs: Dict[str, Any] = None, ...):
    self.skill_configs: Dict[str, Any] = self.conf.get("skill_configs", {})
    
    # Derive MCP servers from skill_configs
    if self.skill_configs:
        self.mcp_servers = replace_mcp_servers_variables(
            self.skill_configs, 
            self.mcp_servers, 
            []
        )
        # Add SKILL tool for activation/offloading
        from aworld.core.context.amni.tool.context_skill_tool import ContextSkillTool
        self.tool_names.extend(["SKILL"])
    
    # Create sandbox with skills
    if self.mcp_servers or self.tool_names:
        self.sandbox = Sandbox(
            mcp_servers=self.mcp_servers,
            mcp_config=self.mcp_config,
            black_tool_actions=self.black_tool_actions,
            skill_configs=self.skill_configs
        )
```

## Data Flow

### Skill State Storage

Skills are stored in the context's namespace storage:

```
context.state[namespace][SKILL_LIST_KEY] = {
    "planning": {
        "name": "Planning",
        "desc": "Task planning...",
        "usage": "Use for...",
        "active": True,
        "tool_list": {...}
    },
    ...
}

context.state[namespace][ACTIVE_SKILLS_KEY] = ["planning", "browser"]
```

### Tool Availability Timeline

```
Time →

t0: Agent initialized
    - skill_configs passed to agent
    - context.init_skill_list() called
    - Skills with "active": True auto-activated
    - SKILL tool registered

t1: System prompt generated
    - SkillsNeuron formats available skills
    - Agent sees skill metadata in prompt

t2: Agent receives task
    - Agent evaluates which skills needed
    - Agent sees SKILL tool available

t3: Agent activates skill
    - Calls: active_skill("browser")
    - Context updates ACTIVE_SKILLS_KEY
    - Returns usage guide to agent

t4: Agent prepares tool call
    - skill_translate_tools() called
    - Filters tools based on active skills
    - Agent receives filtered tool list

t5: Agent uses tools
    - Agent calls browser tools
    - Tools execute normally

t6: Agent offloads skill
    - Calls: offload_skill("browser")
    - Context updates ACTIVE_SKILLS_KEY
    - Subsequent tools filtered without browser
```

## Progressive Disclosure in Action

**Level 1 - Metadata** (Always in prompt):
```xml
<skills_info>
  <skill id="browser">
    <skill_name>Browser</skill_name>
    <skill_desc>Web browsing and automation</skill_desc>
  </skill>
</skills_info>
```

**Level 2 - Activation** (On demand):
```
Agent calls: active_skill("browser")
Response: "skill browser activated... <skill_guide>Use for navigating websites, clicking elements, taking screenshots</skill_guide>"
```

**Level 3 - Tools** (After activation):
```python
Filtered tools: [
    {"function": {"name": "navigate", ...}},
    {"function": {"name": "screenshot", ...}},
    {"function": {"name": "click", ...}},
    ...
]
```

## Namespace Isolation

Each agent has its own skill state:

```python
# Agent 1
context.get_active_skills(namespace="agent1")  # ["planning"]

# Agent 2
context.get_active_skills(namespace="agent2")  # ["browser", "coding"]

# Independent states - no interference
```

## Summary

The Agent Skills implementation follows these principles:

1. **Configuration-based**: Skills defined as Python dicts
2. **Progressive Disclosure**: Metadata → Activation → Tools
3. **Explicit Control**: Agent explicitly activates/offloads skills
4. **Tool Filtering**: Only activated skill tools available
5. **Namespace Isolation**: Each agent manages own skills
6. **Auto-Activation**: Optional automatic activation at initialization
7. **Usage Guidance**: Agent receives context-specific instructions

This design enables flexible, composable agent capabilities while maintaining efficient context window usage.

