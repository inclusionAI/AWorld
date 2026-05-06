# Hybrid Swarm: Peer Message 接收与处理机制

## 问题

在 Hybrid 架构中，当一个 agent 通过 `share_with_peer()` 或 `broadcast_to_all_peers()` 发送消息后，**接收方 agent 如何处理这些消息？**

## 当前实现分析

### 发送端（已实现）

```python
# aworld/core/agent/base.py
async def share_with_peer(self, peer_name: str, information: Any, info_type: str):
    # 1. 通过 EventManager 发送消息
    await self._current_context.event_manager.emit(
        data=share_data,
        sender=self.id(),
        receiver=peer_agent.id(),
        topic=TopicType.PEER_BROADCAST,  # 事件主题
        session_id=self._current_context.session_id,
        event_type=Constants.AGENT
    )
    # 2. 立即返回（非阻塞）
    return True
```

**关键设计：**
- ✅ 使用 `EventManager.emit()` 发送
- ✅ 指定 `receiver=peer_agent.id()`
- ✅ 使用 `TopicType.PEER_BROADCAST` 主题
- ✅ 非阻塞，fire-and-forget 模式

### 接收端（当前状态）

**问题：目前 agent 没有自动消费 peer 消息的机制。**

让我们追踪消息流：

1. **消息发送到 EventBus：**
   ```python
   # aworld/events/manager.py
   async def emit(self, data, sender, receiver, topic, ...):
       event = Message(...)
       await self.event_bus.publish(event)  # 放入队列
   ```

2. **消息存储在队列中：**
   ```python
   # aworld/events/inmemory.py
   async def publish(self, message: Message):
       queue = self._message_queue.get(message.task_id)
       await queue.put(message)  # 消息进入队列
   ```

3. **接收端 agent 需要主动消费：**
   ```python
   # 当前缺失的机制
   message = await context.event_manager.consume()  # ❌ 没有调用
   ```

### 为什么消息没有被处理？

**核心原因：`async_policy()` 中没有消费 peer 消息。**

当前 agent 执行流程：
```python
# aworld/core/agent/base.py:274
async def async_run(self, message: Message, **kwargs):
    observation = message.payload
    result = await self.async_policy(observation, message=message)  # 只处理任务
    return result
```

**Agent 只处理来自 coordinator 的任务观察（observation），不检查 peer 消息队列。**

## 设计原则：机制与策略分离

**框架应该提供机制（Mechanism），而不是强制策略（Policy）。**

### 框架层（Mechanism）- 已提供

**发送能力：**
```python
# BaseAgent 已提供
await self.share_with_peer(peer_name, information, info_type)
await self.broadcast_to_all_peers(information, info_type)
```

**接收能力：**
```python
# 通过 Context 访问 EventManager（已有）
message = await self._current_context.event_manager.consume(nowait=True)

# 或者注册事件处理器（已有）
await self._current_context.event_manager.register(
    event_type=Constants.AGENT,
    topic=TopicType.PEER_BROADCAST,
    handler=my_handler
)
```

**消息格式协议：**
```python
{
    "type": "share" | "broadcast",
    "info_type": str,           # 业务定义的消息类型
    "information": Any,         # 业务数据
    "sender_name": str,
    "timestamp": float
}
```

### Agent 层（Policy）- 自定义

**Agent 自己决定：**
1. ✅ 是否需要处理 peer 消息
2. ✅ 何时处理（任务开始前？执行中？结束后？）
3. ✅ 如何处理（轮询？事件驱动？批量？）
4. ✅ 处理哪些消息（过滤条件）
5. ✅ 如何响应消息（调整策略？记录日志？）

## 使用模式示例

### 模式 1：不处理 peer 消息

```python
class SimpleAgent(Agent):
    """只关注自己的任务，不使用 peer 通信。"""
    async def async_policy(self, observation, **kwargs):
        # 执行任务
        result = self.process(observation.content)
        return [self.to_action_model(result)]
```

### 模式 2：轮询检查（按需查看）

```python
class TransformAgent(Agent):
    """在任务开始时检查来自上游的数据格式信息。"""
    
    async def async_policy(self, observation, **kwargs):
        format_info = None
        
        # 任务开始时检查一次
        if self._is_peer_enabled:
            try:
                msg = await asyncio.wait_for(
                    self._current_context.event_manager.consume(nowait=True),
                    timeout=0.1
                )
                if msg and msg.topic == TopicType.PEER_BROADCAST:
                    payload = msg.payload
                    if payload.get('sender_name') == 'FilterAgent':
                        format_info = payload.get('information')
            except asyncio.TimeoutError:
                pass  # 没有消息，使用默认格式
        
        # 使用 format_info 调整处理逻辑
        result = self.transform(observation.content, format_info)
        return [self.to_action_model(result)]
```

### 模式 3：事件驱动（实时响应）

```python
class MonitorAgent(Agent):
    """实时监控 peer 的告警消息。"""
    
    async def async_policy(self, observation, **kwargs):
        self.alerts = []
        
        # 注册告警处理器
        if self._is_peer_enabled:
            async def handle_alert(msg: Message):
                if msg.topic == TopicType.PEER_BROADCAST:
                    payload = msg.payload
                    if payload.get('info_type') == 'alert':
                        self.alerts.append(payload)
                        logger.warning(f"ALERT: {payload['information']}")
            
            await self._current_context.event_manager.register(
                event_type=Constants.AGENT,
                topic=TopicType.PEER_BROADCAST,
                handler=handle_alert
            )
        
        # 执行监控任务
        result = await self.monitor(observation.content)
        
        # 汇总告警
        return [self.to_action_model({
            "result": result,
            "alerts": self.alerts
        })]
```

### 模式 4：批量处理（周期性检查）

```python
class AggregatorAgent(Agent):
    """收集所有 peer 的状态，汇总后返回。"""
    
    async def async_policy(self, observation, **kwargs):
        peer_statuses = []
        
        if self._is_peer_enabled:
            # 批量读取所有 peer 消息
            timeout = 0.5
            start = time.time()
            
            while time.time() - start < timeout:
                try:
                    msg = await asyncio.wait_for(
                        self._current_context.event_manager.consume(nowait=True),
                        timeout=0.05
                    )
                    if msg and msg.topic == TopicType.PEER_BROADCAST:
                        peer_statuses.append(msg.payload)
                except asyncio.TimeoutError:
                    break
            
            logger.info(f"Collected {len(peer_statuses)} peer messages")
        
        # 汇总分析
        summary = self.aggregate(peer_statuses)
        return [self.to_action_model(summary)]
```

### 模式 5：选择性响应（业务逻辑驱动）

```python
class AdaptiveAgent(Agent):
    """根据消息内容动态调整处理策略。"""
    
    async def async_policy(self, observation, **kwargs):
        # 执行主任务
        result = self.initial_process(observation.content)
        
        # 根据中间结果决定是否查看 peer 消息
        if result.get('quality_score', 1.0) < 0.5:
            logger.info("Quality low, checking peer feedback")
            
            if self._is_peer_enabled:
                try:
                    msg = await asyncio.wait_for(
                        self._current_context.event_manager.consume(nowait=True),
                        timeout=0.1
                    )
                    if msg and msg.payload.get('info_type') == 'feedback':
                        # 根据反馈调整策略
                        result = self.adjust_strategy(result, msg.payload)
                except asyncio.TimeoutError:
                    pass
        
        return [self.to_action_model(result)]
```

## 框架提供的能力总结

### 已提供（完整）

**1. 发送 API：**
```python
# aworld/core/agent/base.py
await agent.share_with_peer(peer_name, information, info_type)
await agent.broadcast_to_all_peers(information, info_type)
```

**2. 消息格式协议：**
```python
{
    "type": "share" | "broadcast",
    "info_type": str,
    "information": Any,
    "sender_name": str,
    "timestamp": float
}
```

**3. 底层访问能力：**
```python
# 通过 Context 直接访问 EventManager
self._current_context.event_manager.consume(nowait=True)
self._current_context.event_manager.register(event_type, topic, handler)
```

**4. Peer 引用：**
```python
self._is_peer_enabled       # 是否在 Hybrid swarm
self._peer_agents          # Dict[agent_id, agent] peer 引用
```

### Agent 层责任

**Agent 自己实现：**
1. ✅ 消息消费逻辑（何时读取）
2. ✅ 消息过滤逻辑（处理哪些）
3. ✅ 业务处理逻辑（如何响应）
4. ✅ 错误处理策略（超时、异常）

**框架不预设：**
- ❌ 默认的消息轮询
- ❌ 自动的消息处理
- ❌ 预定义的业务逻辑

## 最佳实践建议

### 1. 封装 Helper（可选，Agent 层）

如果多个 agent 使用相似的模式，可以在 Agent 层创建 helper：

```python
# my_project/utils/peer_helpers.py

async def poll_peer_messages(agent, filter_type=None, timeout=0.1):
    """通用的消息轮询 helper（项目级别，不是框架）。"""
    if not agent._is_peer_enabled:
        return []
    
    messages = []
    start = time.time()
    while time.time() - start < timeout:
        try:
            msg = await asyncio.wait_for(
                agent._current_context.event_manager.consume(nowait=True),
                timeout=0.05
            )
            if msg and msg.topic == TopicType.PEER_BROADCAST:
                payload = msg.payload
                if filter_type is None or payload.get('info_type') == filter_type:
                    messages.append(payload)
        except asyncio.TimeoutError:
            break
    return messages
```

### 2. 明确消息协议（团队约定）

定义清晰的 `info_type` 语义：

```python
# my_project/constants.py

class PeerMessageType:
    DATA_FORMAT = "data_format"    # 数据格式信息
    STATUS = "status"              # 状态更新
    ALERT = "alert"                # 告警
    FEEDBACK = "feedback"          # 反馈建议
    COMPLETION = "completion"      # 完成通知
```

### 3. 超时与容错

```python
async def async_policy(self, observation, **kwargs):
    peer_info = None
    
    if self._is_peer_enabled:
        try:
            msg = await asyncio.wait_for(
                self._current_context.event_manager.consume(nowait=True),
                timeout=0.1  # 快速超时，不阻塞主任务
            )
            if msg:
                peer_info = msg.payload
        except asyncio.TimeoutError:
            logger.debug("No peer messages, using defaults")
        except Exception as e:
            logger.warning(f"Failed to read peer message: {e}")
    
    # 使用 peer_info 或默认值
    result = self.process(observation.content, peer_info)
    return [self.to_action_model(result)]
```

### 4. 文档化消息依赖

在 Agent 注释中说明消息依赖：

```python
class TransformAgent(Agent):
    """Transform email addresses to lowercase.
    
    Peer Message Dependencies:
    - Consumes: "data_format" from FilterAgent (optional)
    - Produces: "completion" broadcast to all peers
    
    Behavior:
    - If data_format received: adjust transformation strategy
    - If not received: use default strategy
    """
```

## 当前状态与建议

### 框架层（已完成）

**✅ 核心机制已实现：**
1. 发送 API：`share_with_peer()`, `broadcast_to_all_peers()`
2. 事件系统：`EventManager`, `TopicType.PEER_BROADCAST`
3. 消息队列：`InMemoryEventbus`
4. Peer 引用：`_peer_agents`, `_is_peer_enabled`

**不需要添加：**
- ❌ `check_peer_messages()` helper（让 agent 自己实现）
- ❌ `register_peer_handler()` wrapper（直接用 EventManager）
- ❌ 默认消息处理逻辑（策略属于 agent）

### 文档层（需要补充）

**✅ 应该更新：**
1. **README.md**: 说明消息接收是 agent 责任
2. **示例代码**: 展示不同的接收模式（作为参考，不是框架要求）
3. **API 文档**: 明确哪些是框架提供的机制

**示例更新建议：**

```python
# examples/multi_agents/hybrid/data_processing/transform_agent.py

class TransformAgent(Agent):
    """Transform agent - 展示如何消费 peer 消息（一种可选模式）。"""
    
    async def async_policy(self, observation, **kwargs):
        logger.info(f"[{self.name()}] Starting transformation")
        
        # === 可选：检查 peer 消息（这是一种策略，不是框架要求）===
        data_format = None
        if self._is_peer_enabled:
            try:
                # 使用框架提供的 EventManager API
                msg = await asyncio.wait_for(
                    self._current_context.event_manager.consume(nowait=True),
                    timeout=0.1
                )
                if msg and msg.topic == TopicType.PEER_BROADCAST:
                    payload = msg.payload
                    if (payload.get('sender_name') == 'FilterAgent' and 
                        payload.get('info_type') == 'data_format'):
                        data_format = payload.get('information')
                        logger.info(f"[{self.name()}] Got format: {data_format}")
            except asyncio.TimeoutError:
                logger.debug(f"[{self.name()}] No peer messages")
        
        # === 执行主任务 ===
        emails = observation.content
        transformed = [email.lower() for email in emails]
        
        # === 可选：发送完成通知 ===
        if self._is_peer_enabled:
            await self.broadcast_to_all_peers(
                information={"status": "complete", "count": len(transformed)},
                info_type="completion"
            )
        
        return [self.to_action_model(transformed)]
```

### 设计原则总结

| 层级 | 责任 | 不应该做 |
|------|------|----------|
| **框架层** | 提供发送/接收 API | ❌ 预设消息处理逻辑 |
|  | 定义消息格式协议 | ❌ 强制消费模式 |
|  | 管理 peer 引用 | ❌ 提供业务 helper |
| **Agent层** | 决定何时消费消息 | - |
|  | 实现业务处理逻辑 | - |
|  | 定义错误处理策略 | - |
| **项目层** | 封装通用 helper（可选）| - |
|  | 定义消息类型约定 | - |
|  | 编写测试和文档 | - |

## 结论

**当前 Hybrid 实现：机制完整，策略开放。**

**框架已提供：**
- ✅ 完整的 peer 通信机制（发送 + 接收访问能力）
- ✅ 清晰的消息格式协议
- ✅ 非阻塞的设计原则

**Agent 自主决定：**
- ✅ 是否使用 peer 通信
- ✅ 如何消费和处理消息
- ✅ 业务逻辑和错误处理

**下一步建议：**
1. 更新示例代码展示不同的消息消费模式（作为参考）
2. 补充 README 说明机制与策略的分离
3. 添加注释强调"接收是 agent 责任"

**不需要：**
- ❌ 在 BaseAgent 添加 helper 方法
- ❌ 在框架层预设消息处理逻辑
- ❌ 强制 agent 使用特定的消费模式
