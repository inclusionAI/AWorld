在AWorld框架中，类似WorkFlow Construction，MAS构建的基础元素是Agent。通过引入Swarm这一概念，用户可以简单、快速、高效的构建复杂的多Agent系统(Multi-Agent System, MAS)。总结：

1. WorkFlow in AWorld：静态预先设定的执行流程
2. MAS in AWorld：动态实时决策的执行流程

这样的设计，确保了框架底层能力的统一（即 Agent， Graph based Topology），同时兼顾了可扩展性。

# 为什么使用AWorld 构建MAS
+ 便捷。以构图的方式，一行组建multi-agent拓扑。
+ 高效。无依赖的agent和工具自动并行执行。
+ 灵活。不仅可以随时调整策略，还可以任意组合不同类型的swarm。
+ 通用。提供一般性的Workflow和Handoff机制，以支持各种协作范式。
+ 易扩展。可以通过定义自己的Swarm类型实现，并通过build_cls设置。

# 构建MAS
类似Workflow，通过topology我们可以简单的定义Agents之间的通信网络。不一样的是，通过build_type=GraphBuildType.HANDOFF我们允许Agents之间的调用关系可以动态决策。即：

+ agent1可以选择性的决定调用agent2和agent3；调用次数也是动态的，一次或者多次
+ agent2可以选择性的决定调用agent3；调用次数也是动态的，一次或者多次

```python
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners


agent_conf = AgentConfig(...)
agent1 = Agent(name="agent1", conf=agent_conf)
agent2 = Agent(name="agent2", conf=agent_conf)
agent3 = Agent(name="agent3", conf=agent_conf)
swarm = Swarm(topology=[(agent1, agent2), (agent2, agent3), (agent1, agent3)], 
              build_type=GraphBuildType.HANDOFF)

Runners.run(input="your question", swarm=swarm)
```

## 指定入口Agent
因为MAS定义的时候本质是一个Graph，不同的Agent都可以接受外部的输入，我们可以通过root_agent参数指定接受query的Agent。

```bash
swarm = Swarm(topology=[(agent1, agent2), (agent2, agent3), (agent1, agent3)], 
              build_type=GraphBuildType.HANDOFF, root_agent=agent1)
```

## 更加简便的拓扑结构表达
```plain
                ┌────── agent1 ──────┐
          ┌── agent2              agent3
  ┌───agent4────┐                  ┌┘
agent6        agent7             agent5
  └─────────────└──────agent8───────┘
```

```python
swarm = Swarm(topology=[
    (agent1, agent2), (agent2, agent3), (agent2, agent4), 
    (agent3, agent5), (agent4, agent6), (agetn4, agent7), 
    (agent6, agent8), (agent7, agent8), (agent5, agent8)
], build_type=GraphBuildType.HANDOFF, root_agent=agent1)
```

上面是标准的构建方式，但相对繁琐，因此定义了并行[]和串行()语义，分别表示并行和串行。

```bash
# Workflow-specific syntax simplification construction
swarm = Swarm(topology=[agent1, [(agent2, (agent4, [agent6, agent7])), (agent3, agent5)], agent8],
              build_type=GraphBuildType.HANDOFF, 
              root_agent=agent1)
```

## Dynamic Routing
当def policy()决策下一步要调用的智能体时，对于一些特殊情况，Agent需要根据一定的业务规则做定制化的路由，你可在对应的Agent复写handler即可。

```python
# your_handler_name consistency must be maintained
agent = Agent(..., event_handler_name="your_handler_name")
```

```python
@HandlerFactory.register(name="your_handler_name")
class YourHandler(DefaultHandler):
    def is_valid_message(self, message: Message):
        if message.category != "your_handler_name":
            return False
        return True

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if not self.is_valid_message(message):
            return

        # the type of data is generally ActionModel，also can be comtumized
        data = message.payload
        if "clause1" in data:
            pass
        elif "clause2" in data:
            pass
```

在AWorld中可以参考<font style="color:#000000;background-color:#ffffff;">DefaultTaskHandler的实现。</font>

### 复写Routing的两个例子：ReAct和Plan-Execute
拓扑结构如下：

```plain
            ┌────── agent1 ──────┐
           agent2              agent3

```

#### ReAct
```python
@HandlerFactory.register(name=f'react')
class PlanHandler(AgentHandler):
    def is_valid_message(self, message: Message):
        if message.category != 'react':
            return False
        return True

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        yield message
```

#### Plan-Execute
对比ReAct，agent2和agent3可以同时并行执行

```python
@HandlerFactory.register(name=f'plan_execute')
class PlanHandler(AgentHandler):
    def is_valid_message(self, message: Message):
        if message.category != 'plan_execute':
            return False
        return True

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        logger.info(f"PlanHandler|handle|taskid={self.task_id}|is_sub_task={message.context._task.is_sub_task}")
        content = message.payload
        
        # parse model plan
        plan = parse_plan(content[0].policy_info)
        logger.info(f"PlanHandler|plan|{plan}")
        
        # execute steps
        output, context = execution_steps(plan.steps)

        # send event message, notify the next processing agent
        new_plan_input = Observation(content=output)
        yield AgentMessage(session_id=message.session_id,
                           payload=new_plan_input,
                           sender=self.name(),
                           receiver=self.swarm.communicate_agent.id(),
                           headers={'context': context})
```

更多细节可以参考[examples](https://github.com/inclusionAI/AWorld/blob/main/examples/multi_agents/coordination/deepresearch/planner/plan_handler.py).

# <font style="color:#000000;background-color:#ffffff;">MAS和Workflow的组合与递归</font>
相同或不同类型的Swarm可以深层嵌套，提供多层级不同交互机制的Swarm，以支持复杂的multi-agent交互。如做一个旅游行程规划，使用**Workflow + Team**的结合，Workflow提供确定性的流程，Team做多源信息的检索和整合。

```python
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm

# five agents
rewrite = Agent(name="rewrite", conf=agent_conf)
plan = Agent(name="plan", conf=agent_conf)
search = Agent(name="search", conf=agent_conf)
summary = Agent(name="summary", conf=agent_conf)
report = Agent(name="report", conf=agent_conf)

# construct a MAS
mas = Swarm(topology=[(plan, search), (plan, summary)], 
            build_type=GraphBuildType.HANDOFF, root_agent=plan)

# construct a combination of a workflow with the mas team
combination = Swarm([(rewrite, mas), (mas, report)], root_agent=[rewrite])
```

即首先对用户的输入做rewrite，然后通过TeamSwarm得到综合结果，最终由report Agent输出结果。

