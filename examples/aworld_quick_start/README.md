# AWorld tutorial

## Concept

There are many concepts in AWorld, such as Sandbox, LLModel, etc,

The most important ones are Agent and Swarm, Tool and MCP, Task and Runner, Memory and Context.
In AWorld, these four pairs may cause confusion, but they actually represent different concepts and entities.

- `Agent` refers to an agent in the conventional sense, while `Swarm` denotes the topological structure of multi-agents.
- `Tool` refers to a tool, while `MCP` is a specific form of tool with standard protocol.
- `Task` refers to task definition, while `Runner` is the executor of task (Task).
- `Memory` is the entity of memory of agent operations, while `Context` is a more fine-grained and higher-dimensional
  contextual entity.

## `aworld_quick_start` directory

Each example directory contains a script named "run.py" for running the example.
The prerequisite for running is to copy the .env_template file to a .env file and fill in the corresponding parameters.

### Examples of task execution

- [Use Agent](define_agent)\
  Example of single-agent operation. Used to define a scenario where an agent employs an LLM.


- [Use tool](local_tool) \
  Example of a single-agent using a tool to perform tasks. Used in scenarios where a simple custom tool is required.


- [Use MCP](mcp_tool) \
  Example of using the MCP tool for single-agent tasks. Intended for scenarios where the MCP tool is required.

- [Use PTC](ptc) \
  Example of using the PTC (Programmatic Tool Calling) tool for agent tasks. Intended for scenarios where the PTC tool is required.


- [Human-in-the-loop](HITL) \
  The example mainly demonstrates how to use the AWorld framework for human-machine collaboration.


- [Agent handoff](handoff)\
  An example of agent-coordinated handover mode, where agents are executed as tools. Used in multi-agent scenarios.


- [Agent workflow](workflow)
  Example of agent-based collaborative workflow, where agents execute as independent entities. Used in multi-agent
  scenarios.


- [Hierarchical swarm](hybrid_swarm) \
  A simple example of hybrid swarm. Used in complex multi-agent interaction scenarios, such as when a node in a workflow
  is also a multi-agent.


- [Parallel task execution](parallel_task) \
  When executing multiple tasks simultaneously, different execution backends can be selected. It is mainly used in
  scenarios where simple multiple queries are performed to obtain their responses.


- [Run by configuration](run_by_config) \
  Demo. Load the configuration file, generate an agent or swarm, and run it.


- [CLI运行](cli)\
  Example of CLI. Used for scenarios where the AWorld framework is used through the command line interface.

### Examples of using independent modules

- [Use Sandbox](env)\
  Example of using the Sandbox module. Used for connecting and operating on remote tool services.


- [Use memory](use_memory)
  Read-Write Memory example. Used for customizing the operation of Memory.


- [Using LLM Tools](use_llm)
  Example of using LLM utilities. For directly utilizing LLM.


- [Use Trace](use_trace)
  Enable the trace example. It is used to view the execution process and debug.
