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

> 说明：Runner 作为全局调度者，直接管理和访问 RunnerContext，统一调度多智能体的协作流程。RunnerContext 作为多智能体协作的全局上下文，支持任务信息、会话、用户画像、事实知识、运行时状态、输出结果等的集中管理与共享。

---

# DEEPSEARCH Multi-Agent Case Study

## Agent Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│                              Runner                                        │
│  (Orchestrates PlanAgent → Multiple SearchAgents → ReportAgent workflow)   │
└────────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                           RunnerContext                                   │
│  (Shared Memory for tasks, search results, and final report)              │
└────────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  ┌─────────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │  PlanAgent      │  │SearchAgent1 │  │SearchAgent2 │  │SearchAgentN │  │
│  │   Instance      │  │  Instance   │  │  Instance   │  │  Instance   │  │
│  │                 │  │             │  │             │  │             │  │
│  │ ┌─────────────┐ │  │┌───────────┐│  │┌───────────┐│  │┌───────────┐│  │
│  │ │AgentContext │ │  ││AgentContext││  ││AgentContext││  ││AgentContext││  │
│  │ │             │ │  ││           ││  ││           ││  ││           ││  │
│  │ │ • Working   │ │  ││ • Working ││  ││ • Working ││  ││ • Working ││  │
│  │ │   Memory    │ │  ││   Memory  ││  ││   Memory  ││  ││   Memory  ││  │
│  │ │ • Short-term│ │  ││ • Short-  ││  ││ • Short-  ││  ││ • Short-  ││  │
│  │ │   Memory    │ │  ││   term    ││  ││   term    ││  ││   term    ││  │
│  │ │ • Long-term │ │  ││   Memory  ││  ││   Memory  ││  ││   Memory  ││  │
│  │ │   Memory    │ │  ││ • Long-   ││  ││ • Long-   ││  ││ • Long-   ││  │
│  │ └─────────────┘ │  ││   term    ││  ││   term    ││  ││   term    ││  │
│  │                 │  ││   Memory  ││  ││   Memory  ││  ││   Memory  ││  │
│  │ • Generate N    │  │└───────────┘│  │└───────────┘│  │└───────────┘│  │
│  │   tasks         │  │             │  │             │  │             │  │
│  │ • Distribute    │  │ • Execute   │  │ • Execute   │  │ • Execute   │  │
│  │   tasks         │  │   task_1    │  │   task_2    │  │   task_N    │  │
│  │ • Coordinate    │  │ • Return    │  │ • Return    │  │ • Return    │  │
│  │   workflow      │  │   artifact  │  │   artifact  │  │   artifact  │  │
│  └─────────────────┘  └─────────────┘  └─────────────┘  └─────────────┐  │
│                                                                        │  │
│  ┌─────────────────┐                                                  │  │
│  │  ReportAgent    │                                                  │  │
│  │   Instance      │                                                  │  │
│  │                 │                                                  │  │
│  │ ┌─────────────┐ │                                                  │  │
│  │ │AgentContext │ │                                                  │  │
│  │ │             │ │                                                  │  │
│  │ │ • Working   │ │                                                  │  │
│  │ │   Memory    │ │                                                  │  │
│  │ │ • Short-term│ │                                                  │  │
│  │ │   Memory    │ │                                                  │  │
│  │ │ • Long-term │ │                                                  │  │
│  │ │   Memory    │ │                                                  │  │
│  │ └─────────────┘ │                                                  │  │
│  │                 │                                                  │  │
│  │ • Generate      │                                                  │  │
│  │   final report  │                                                  │  │
│  │ • Synthesize    │                                                  │  │
│  │   all findings  │                                                  │  │
│  └─────────────────┘                                                  │  │
│                                                                        │  │
└────────────────────────────────────────────────────────────────────────────┘
```

## Workflow Details

### 1. PlanAgent Execution
- **1.1** Generate N tasks based on user requirements
- **1.2** Create N tasks and save to WorkingMemory in RunnerContext
- **1.3** Distribute tasks to multiple SearchAgents (parallel execution)
- **1.4** Collect all SearchAgent results and artifacts
- **1.5** Call ReportAgent to generate final output

### 2. SearchAgents Execution (Parallel)
- **2.1** Each SearchAgent receives assigned task from PlanAgent
- **2.2** Execute search queries and gather relevant materials
- **2.3** Organize and process collected information
- **2.4** Generate and return structured artifacts to PlanAgent

### 3. ReportAgent Execution
- **3.1** Receive all collected artifacts from multiple SearchAgents
- **3.2** Analyze and synthesize findings from multiple sources
- **3.3** Generate comprehensive final report
- **3.4** Save report to RunnerContext outputs

## Data Flow in RunnerContext

```
WorkingMemory:
├── tasks: [task1, task2, ..., taskN] 
├── task_execution_steps: [
│   ├── step1: "PlanAgent generated N tasks",
│   ├── step2: "Created tasks in WorkingMemory", 
│   ├── step3: "Distributed tasks to SearchAgents",
│   ├── step4: "SearchAgent1 executing task_1",
│   ├── step5: "SearchAgent2 executing task_2",
│   ├── step6: "SearchAgent1 completed task_1",
│   └── ...
│ ]
├── task_status: {
│   ├── task_1: "completed by SearchAgent1",
│   ├── task_2: "completed by SearchAgent2", 
│   └── task_3: "in_progress by SearchAgent3"
│ }
└── search_artifacts: [artifact1, artifact2, ..., artifactN]

Outputs:
└── final_report: Comprehensive analysis and recommendations

Artifacts:
├── search_artifacts: [Multiple SearchAgents generated artifacts]
├── execution_logs: [Detailed step-by-step execution records]
└── final_report_artifact: ReportAgent generated final report
```

## Key Features

- **Sequential Coordination**: PlanAgent orchestrates the entire workflow
- **Parallel Search**: Multiple SearchAgents work simultaneously for better performance
- **Centralized Memory**: All data flows through RunnerContext for consistency
- **Modular Design**: Each agent has specific responsibilities and clear interfaces
- **Scalable Architecture**: Can easily add more SearchAgents for increased throughput