# Agent调用关系跟踪模块

## 概述

Agent调用关系跟踪模块是一个用于记录和可视化多Agent系统中Agent之间调用关系的工具。它能够追踪直接调用和作为工具的调用，并构建调用层次结构，帮助开发者理解和分析复杂的多Agent系统。

## 功能特点

- 跟踪Agent之间的直接调用关系
- 跟踪Agent作为工具被调用的关系
- 构建Agent调用层次结构
- 计算每个Agent的级别
- 生成可视化的调用关系图
- 导出调用关系数据为JSON格式
- 与AWorld事件系统无缝集成

## 设计架构

### 核心组件

1. **AgentCallTracker**：负责跟踪和记录Agent之间的调用关系
2. **AgentCallTrackingMiddleware**：作为事件系统的中间件，通过transformer机制拦截和处理Agent消息
3. **AgentCallTrackingService**：提供全局单例访问AgentCallTracker的服务
4. **AgentCallVisualizer**：生成可视化的调用关系图
5. **AgentCall**：表示一次Agent调用的数据结构
6. **CallHierarchyNode**：表示调用层次结构中的节点

### 实现原理

中间件通过事件系统的transformer机制拦截Agent消息。具体实现如下：

1. 在TaskEventRunner初始化时，创建AgentCallTrackingMiddleware实例
2. 在TaskEventRunner.pre_run方法中，调用中间件的register_to_event_manager方法
3. 中间件通过event_manager.register_transformer方法注册为Agent类型消息的transformer
4. 当Agent消息通过事件系统时，中间件的intercept_message方法会被调用
5. intercept_message方法从消息中提取sender、receiver和agent_as_tool标记，并记录调用关系

### 调用关系类型

模块区分两种调用关系：

1. **直接调用（agent_direct_call）**：Agent直接调用另一个Agent
2. **作为工具调用（as_tool）**：Agent将另一个Agent作为工具调用

### 调用层次结构

模块构建一个树形的调用层次结构，其中：

- 根节点是级别为0的Agent
- 子节点是被调用的Agent
- 每个节点的级别由其在调用链中的深度决定

## 使用方法

### 基本用法

1. 创建TaskEventRunner时，模块会自动初始化和注册
2. 任务执行完成后，调用关系图会自动生成并保存到任务配置的output_dir目录中

```python
from aworld.runners.event_runner import TaskEventRunner
from aworld.core.task import Task

# 创建任务
task = Task(
    id="my_task",
    name="My Task",
    conf={
        "output_dir": "./output"
    }
)

# 创建任务运行器
runner = TaskEventRunner(task=task, swarm=my_swarm)

# 运行任务
await runner.reset("用户输入")
await runner.do_run()

# 调用关系图已自动生成到./output目录
```

### 手动获取调用关系

```python
from aworld.core.tracking.agent_call_middleware import AgentCallTrackingService

# 获取全局AgentCallTracker实例
tracker = AgentCallTrackingService.instance()

# 获取调用关系图
call_graph = tracker.get_call_graph()

# 获取Agent级别
level = tracker.get_agent_level("agent_id")

# 获取根Agent
root_agents = tracker.get_root_agents()

# 获取Agent的子Agent
children = tracker.get_agent_children("agent_id")
```

### 手动生成可视化

```python
from aworld.core.tracking.visualizer import AgentCallVisualizer
from aworld.core.tracking.agent_call_middleware import AgentCallTrackingService

# 获取全局AgentCallTracker实例
tracker = AgentCallTrackingService.instance()

# 创建可视化器
visualizer = AgentCallVisualizer(tracker)

# 导出可视化
output_dir = "./output"
task_id = "my_task"
visualization_files = visualizer.export_visualization(output_dir, task_id)

# 获取生成的文件路径
html_path = visualization_files["html_path"]
json_path = visualization_files["json_path"]
```

## 示例

参见`examples/agent_call_tracking_demo.py`，这是一个完整的演示示例。

## 调用关系图示例

生成的HTML调用关系图包含以下内容：

1. **Agent级别信息**：显示每个Agent的级别
2. **调用关系图**：使用Mermaid.js生成的可视化图表
   - 实线箭头表示直接调用
   - 虚线箭头表示作为工具调用
   - 不同颜色表示不同级别的Agent

## 注意事项

1. 确保在`DefaultAgentHandler._agent`方法中正确设置了`agent_as_tool`标记
2. 调用关系图的生成依赖于正确的消息传递，确保消息中包含正确的sender和receiver信息
3. 可视化需要访问外部CDN加载Mermaid.js，如果在离线环境使用，请修改visualizer.py中的HTML模板

## 未来改进

1. 支持更复杂的调用关系分析
2. 提供更多可视化选项
3. 集成到AWorld的Web UI中
4. 支持实时监控Agent调用关系 