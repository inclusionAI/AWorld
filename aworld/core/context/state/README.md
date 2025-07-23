# Aworld Context

## Single Agent

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  ┌─────────────────────────────┐                             │
│  │                             │                             │
│  │        AgentContext         │  Composed of:               │
│  │                             │                             │
│  │  ┌────────────────────────────────────────────────────┐   │
│  │  │ Working Memory                                     │   │
│  │  │   - agent_id              (str)                    │   │
│  │  │   - agent_config          (AgentConfig)            │   │
│  │  │   - context_rule          (ContextRuleConfig)      │   │
│  │  │   - context_usage         (ContextUsage)           │   │
│  │  │   - artifacts             (AIGC Artifacts)         │   │
│  │  └────────────────────────────────────────────────────┘   │
│  │  ┌────────────────────────────────────────────────────┐   │
│  │  │ Short-term Memory                                  │   │
│  │  │   - conversation_history  (list)                   │   │
│  │  │   - summary                (list/str)              │   │
│  │  └────────────────────────────────────────────────────┘   │
│  │  ┌────────────────────────────────────────────────────┐   │
│  │  │ Long-term Memory                                   │   │
│  │  │   - experiences           (list)                   │   │
│  │  │   - facts                 (list)                   │   │
│  │  └────────────────────────────────────────────────────┘   │
│  │                             │                             │
│  └─────────────────────────────┘                             │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

- **Working Memory**: Contains `agent_id`, `agent_config`, `context_rule`, `context_usage`, and `artifacts`. Manages the agent's current state, configuration, and generated artifacts.
- **Short-term Memory**: Contains `conversation_history` and `summary`. Stores recent conversations, context, and summaries for immediate reference.
- **Long-term Memory**: Contains `experiences` and `facts`. Stores agent's accumulated experiences and long-term knowledge/facts.

All these components together form the complete **AgentContext** for each agent.

# Multi-Agent Context Overall Structure

```
┌────────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                              Runner                                  │  │
│  │  (Global coordinator, manages and accesses RunnerContext,            │  │
│  │   orchestrates all agents and workflow)                              │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                       │
│                                    │                                       │
│                                    ▼                                       │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                           RunnerContext                              │  │
│  │  (Accessible by Runner and all agents, contains global and runtime   │  │
│  │   information for multi-agent collaboration)                         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                       │
│                                    │                                       │
│                                    ▼                                       │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                                                                      │  │
│  │  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────┐  │  │
│  │  │                    │  │                    │  │                │  │  │
│  │  │  AgentContext #1   │  │  AgentContext #2   │  │  AgentContext#3│  │  │
│  │  │                    │  │                    │  │                │  │  │
│  │  └────────────────────┘  └────────────────────┘  └────────────────┘  │  │
│  │                                                                      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## RunnerContext

- **task**: Task object reference, allows all agents or cells to access and operate on global task information.
- **session**: Session management, records all conversation history, actions, and reasoning steps; supports context sharing in multi-agent collaboration.
- **user_profile**: User profile and preferences, supports long-term memory and personalized service.
- **facts**: Relation facts, serves as a long-term knowledge/fact base for agent reasoning.
- **custom_information**: Retrieved information and references, stores external knowledge and temporary data for flexible extension.
- **working_state**: Stores the current task state and intermediate results, accessible by both Runner and all agents. Used for sharing the latest progress, temporary variables, and intermediate outputs during task execution.
- **outputs**: Output buffer for synthesized results and conclusions, shared among agents and Runner for collaborative decision-making.
- **artifacts**: Stores AIGC artifacts generated during the task, available for all agents to access and utilize.
- **context_usage**: Tracks context usage statistics and resource consumption, helping optimize memory and context management.
- **trace_state**: Maintains trace and token tracking information for monitoring and debugging multi-agent workflows.