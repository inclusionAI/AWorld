# Welcome to AWorld

**AWorld** is a friendly and streamlined foundational framework for building, orchestrating, evaluating, and evolving AI Agents. AWorld provides a comprehensive suite of basic and advanced components that empower self-evolving AI Agents to handle a wide variety of tasks.

---

## What is AWorld?

AWorld is an end-to-end multi-agent framework designed to help you effortlessly:

- 🤖 **Build intelligent agents** — declaratively create LLM-based AI agents
- 🔄 **Orchestrate multi-agent collaboration** — construct complex multi-agent systems using handoffs and workflows
- 📚 **Capture full execution traces** — log detailed, token-level execution trajectories
- 🌍 **Interact with diverse environments** — access extensible tool environments and connect to third-party systems
- 🧠 **Enable self-improvement** — allow agents to learn from experience and continuously optimize their behavior

---

## Core Concepts

Based on rules or models, entities can reason, use tools, and collaborate with other intelligent agents.

### Fundamental Components

1. **Agent**: An entity, rule-based or model-driven, that can reason, use tools, and collaborate with other agents.

2. **Swarm**: A unified topological model for defining, constructing, and managing collaborative patterns among multiple agents (Multi-Agent systems, MAS).

3. **Tool**: An external capability that extends beyond the native abilities of an LLM, enabling agents to perform more complex and diverse tasks.

4. **Sandbox**: A secure environment where agents can execute code or interact with external software safely.

5. **Runner**: AWorld's scheduling and execution engine, responsible for task dispatching, state transitions, parallel/sequential execution, and recording interaction traces.

6. **Context**: Manages the execution context of a task, supporting state tracking and recovery.

7. **Memory**: Stores and retrieves information generated during agent execution, supporting both short-term and long-term memory management.

8. **Environment**: Provides APIs, tools, and resources that agents can interact with.

9. **Trainer**: A module for training and optimizing agent behavior, typically leveraging reinforcement learning or other machine learning techniques.

10. **Evaluation**: A framework for assessing agent capabilities through standardized metrics and benchmarks.

11. **Tracer**: Tracks fine-grained steps and state changes throughout agent execution for debugging and analysis.

12. **Output**: Manages system outputs, including logs, reports, and visualized data.

### Extended Concepts

Under these core concepts, several sub-concepts further enrich the framework:

- An **Agent** leverages **Models** and **MCP tools** to accomplish tasks.
- A **Tool** consists of one or more executable **Actions**.
- The **Runner** uses an **event-driven** architecture to orchestrate **Tasks** via **Handlers** and **Callbacks** with support for injecting **Hooks** at various points in the execution pipeline.

---

## Architecture

AWorld's typical runtime architecture forms a feedback control loop:

1. **Perceive**: The current state of the Environment (**Observation**) is passed to the Agent.
2. **Decide**: The Agent—leveraging its underlying model, rules, and context—generates the next action (**Action**).
3. **Act**: The Environment executes the specified Action, producing a new Observation and feedback (**Reward**).
4. **Experience**: Through interaction with the environment, the Agent collects trajectory data (**Trajectory**) that captures both successful and failed attempts.
5. **Learn**: These trajectories are logged and used for subsequent **optimization** via reinforcement learning (RL) or in-context learning, enabling the Agent to continuously self-improve and evolve.

![AWorld Architecture](../imgs/aworld.png)

---

## Key Features

AWorld is designed to balance flexibility with production-grade performance, offering a rich set of features to better support agent-based applications and products:

### 1. Simple, Flexible, and Powerful Multi-Agent System
Enables both centralized and decentralized multi-agent systems through a unified graph-based syntax. Supports composition and nesting of agents, and provides built-in patterns such as **Workflow**, **Handoff**, and **Team** for constructing sophisticated collaborative systems.

### 2. Rich Tool-usage Paradigms
Supports multiple tool integration modes:
- Local tools
- MCP (Model Context Protocol) tools
- Agent-as-a-Tool
- Programmatic Tool Calling (PTC)

### 3. Diverse Runtime Backends
Compatible with multiple runtime backends via a unified interface, including Local process, Apache Spark, and Ray. Switch between environments seamlessly without modifying task definitions.

### 4. Broad LLM Provider Support
Integrates with major LLM providers such as OpenAI, Anthropic, Qwen, and more. Models can be easily swapped or configured through a consistent API.

### 5. Event-Driven Architecture
Built on an event-driven execution model where dedicated handlers process events by type. This enables modularity, extensibility, and concurrent processing of execution steps.

### 6. Context Management
Unified management of context across tasks, events, and multi-turn conversations, with built-in capabilities for compression, filtering, and state recovery to ensure efficient tracking.

### 7. Trajectory Construction
The runtime captures complete execution trajectories for every task—including every LLM call, action, and reward—enabling synthesis of training samples, performance evaluation, and iterative improvement.

### 8. Environment Support
Provides external or simulated environments that agents can perceive, interact with, learn from, and act within. Supports complex real-world online environment access.

### 9. Data Generation
Includes built-in data generation capabilities to produce high-quality training data from agent execution trajectories, facilitating model training and optimization.

### 10. Self-Evolution Capability
Supports meta-learning–based self-evolution mechanisms that optimize the entire agent system, not just model weights, enabling continuous adaptation and improvement.

### 11. Evaluation
Offers a comprehensive evaluation suite to assess agent capabilities across multiple dimensions, supporting standard benchmarks and custom metrics.

### 12. Servicification Support
Allows agents and components to be deployed as services with exposed APIs, enabling integration into larger systems.

### 13. Visualization Support
Records and persists key interaction data during task execution for UI rendering, analysis, and user inspection.

### 14. Observability
Features a built-in tracing framework that logs detailed information about agents, tool invocations, and multi-agent interactions—greatly simplifying debugging and monitoring.

### 15. High Extensibility
Modular by design, AWorld supports extension at every layer making it adaptable to diverse use cases, including agents, tools, memory, runners, and more.

### 16. Multiple Usage Modes
Available via **SDK**, **CLI**, and **Web UI**, catering to developers, researchers, and end users alike.

---

## Get Started

Ready to build your first agent? Check out our [Quick Start Guide](Quick%20Start.md) to get up and running in minutes.

For detailed information on each component, explore our comprehensive documentation:

- [Build Your First Agent](../Agents/Build%20Agent.md)
- [Runtime Overview](../Agents/Runtime/Overview.md)
- [Environment Setup](../Environment/Overview.md)

---

Together, these features make AWorld a powerful and flexible framework for self-evolving agents, equally suited for simple single-agent applications and complex, large-scale multi-agent collaboration systems.

