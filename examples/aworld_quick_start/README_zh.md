# AWorld教程

## 概念

AWorld中有较多的概念，如Sandbox, LLModel等，
其中最重要的是Agent和Swarm，Tool和MCP，Task和Runner，Memory和Context。
在AWorld中，这四对可能会引起混淆，实际表示不同的概念和实体。

- `Agent`是常规意义的代理，`Swarm`是表示多Agent的拓扑结构。
- `Tool`是工具，`MCP`是具有标准协议的工具的一种形态。
- `Task`是任务定义，`Runner`是任务(Task)执行器。
- `Memory`是记忆操作的实体，`Context`是更细粒度和更高维度的上下文实体。

## `aworld_quick_start` 目录

各示例目录下均有一个run.py的脚本，用于运行示例。
运行的前提是需要将.env_template文件复制为.env文件，并填写相应的参数。

### 任务执行示例

- [使用Agent](define_agent)\
  Single-agent的运行示例。用于定义一个使用LLM的agent的场景。


- [使用工具](local_tool) \
  Single-agent使用工具执行任务示例。用于需要使用简单自定义工具场景。


- [使用MCP](mcp_tool) \
  Single-agent使用MCP工具执行任务示例。用于需要使用MCP工具的场景。


- [使用PTC](ptc) \
  Aagent使用PTC(Programmatic Tool Calling)执行任务示例。用于需要使用PTC工具的场景。


- [人机协同](HITL) \
  示例主要展示了如何使用AWorld框架做人机协同。用于人机交互的场景。


- [Agent交接](handoff)\
  agent协同的交接模式示例，agent作为工具的方式执行。用于multi-agent场景。


- [Agent工作流](workflow)\
  agent协同的工作流方式示例，agent作为独立主体执行。用于multi-agent场景。


- [层级Swarm](hybrid_swarm) \
  混合swarm简单示例。用于复杂的multi-agent交互场景，比如工作流中的某个节点也是一个multi-agent。


- [多任务并行执行](parallel_task) \
  多个任务同时执行，可以选择不同的执行backend。主要用于简单的多query获取其回复的场景。


- [基于配置运行](run_by_config) \
  Demo功能。加载配置文件，生成agent或swarm，并运行。


- [CLI运行](cli)\
  使用CLI示例。用于通过命令行交互的场景。

### 模块使用示例

- [使用Sandbox](env)\
  sandbox示例。用于连接和操作MCP等服务的工具环境。


- [使用Memory](use_memory)\
  读写Memory示例。用于自定义操作Memory。


- [使用LLM工具](use_llm)\
  使用LLM工具示例。用于直接使用LLM。


- [使用Trace](use_trace)\
  启用trace示例。用于查看执行过程和debug。
