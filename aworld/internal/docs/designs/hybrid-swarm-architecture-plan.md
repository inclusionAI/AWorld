# Hybrid Swarm Architecture Implementation Plan

**Branch:** `feat/mas-architecture-improvements`  
**Date:** 2026-04-03  
**Author:** Claude Code  
**Status:** Planning

## Executive Summary

基于 Google Research 论文 "Towards a science of scaling agent systems: When and why agent systems work" 的研究成果，本计划提出在 AWorld 框架中实现 Hybrid 拓扑架构，以支持层级监督 (hierarchical oversight) 与点对点协作 (peer-to-peer coordination) 的混合模式。

## 1. Research Background

### 1.1 论文核心发现

**五种经典架构对比：**

| Architecture | Description | Strengths | Weaknesses |
|--------------|-------------|-----------|------------|
| Single-Agent (SAS) | 单一代理顺序执行 | 简单、低开销 | 无法并行、缺乏专业化 |
| Independent | 并行多代理、无通信 | 最大并行化 | 错误放大 17.2x |
| Centralized | 中心编排器 + 工作代理 | 错误放大仅 4.4x、适合可并行任务 | 通信瓶颈、中心故障点 |
| Decentralized | 点对点网状通信 | 灵活、无中心故障点 | 高通信开销、协调复杂 |
| **Hybrid** | 层级监督 + 点对点协作 | 平衡控制与灵活性 | 实现复杂度高 |

**关键量化原则：**

1. **对齐原则 (Alignment Principle):**
   - 可并行任务：Centralized 架构性能提升 80.9%
   - 示例：财务分析（收入趋势、成本结构、市场对比可独立分析）

2. **顺序惩罚 (Sequential Penalty):**
   - 严格顺序推理任务：所有多代理架构性能下降 39-70%
   - 示例：PlanCraft 规划任务（步骤间强依赖）

3. **工具协调权衡 (Tool-Coordination Trade-off):**
   - 工具数量增加时，多代理协调开销显著增大
   - 16+ 工具时，协调成本超过并行收益

4. **错误放大特性：**
   - Independent: 17.2x 错误放大
   - Centralized: 4.4x 错误放大（编排器充当验证瓶颈）

5. **预测模型：**
   - R² = 0.513 的预测模型
   - 87% 准确率识别最优架构
   - 基于任务属性：工具数量、可分解性、顺序依赖

### 1.2 Hybrid 架构定义

**论文描述：**
> "A combination of hierarchical oversight and peer-to-peer coordination to balance central control with flexible execution."

**核心特征：**
- **Hierarchical Oversight:** 中心协调器监督整体流程
- **Peer-to-peer Coordination:** 执行代理间可直接通信
- **Dynamic Balance:** 根据任务特性动态调整控制程度

**通信开销：** O(r × n + p × m)
- r: 编排器轮次
- n: 代理数量
- p: 点对点通信轮次
- m: 平均每轮点对点请求数

## 2. AWorld Current Architecture

### 2.1 现有拓扑类型

**GraphBuildType Enum:**
```python
class GraphBuildType(Enum):
    WORKFLOW = "workflow"   # 确定性流程编排
    HANDOFF = "handoff"     # AI驱动的代理移交
    TEAM = "team"           # 领导-执行者模式
```

**Architecture Mapping:**

| AWorld Type | 论文类型 | 对应关系 |
|-------------|----------|----------|
| WORKFLOW | N/A | 不在论文范围（确定性 DAG） |
| HANDOFF | Decentralized | 点对点 AI 驱动移交 |
| **TEAM** | **Centralized** | ✅ **中心编排器（hub-and-spoke）** |
| **HYBRID** | **Hybrid** | **缺失** ❌ |

**关键映射确认：**
- **TeamSwarm = Centralized Architecture** from the paper
- TeamSwarm 作为 hub-and-spoke 模式，已验证有 80.9% 性能提升（可并行任务）
- Hybrid 需在 TeamSwarm 基础上添加 peer-to-peer 能力

### 2.2 现有 Builder 架构

**Builder 类层次：**
```
TopologyBuilder (抽象基类)
├── WorkflowBuilder    → 支持 DAG 拓扑、串并混合
├── HandoffBuilder     → 代理对 (agent pairs)、全连接/环形
└── TeamBuilder        → 星型拓扑、单一 root_agent
```

**AgentGraph 核心数据结构：**
```python
class AgentGraph:
    build_type: str                           # 构建类型
    root_agent: BaseAgent                     # 根代理
    ordered_agents: List[BaseAgent]           # 拓扑序列
    agents: OrderedDict[str, BaseAgent]       # 代理节点
    predecessor: Dict[str, Dict[str, EdgeInfo]]  # 前驱边
    successor: Dict[str, Dict[str, EdgeInfo]]    # 后继边
    has_cycle: bool                           # 是否包含环
```

### 2.3 Gap Analysis

**缺失功能：**

1. ❌ **Hybrid Build Type:** 没有 `GraphBuildType.HYBRID`
2. ❌ **HybridBuilder:** 没有实现混合架构构建器
3. ❌ **HybridSwarm:** 没有对应的 Swarm 子类
4. ❌ **Peer-to-peer in Hierarchy:** TEAM 模式不支持执行器间通信
5. ❌ **Task Property Detection:** 没有任务属性分析（顺序性、可并行性）
6. ❌ **Dynamic Architecture Selection:** 没有根据任务特性自动选择架构

**现有可复用组件：**

✅ **AgentGraph:** 支持任意有向图拓扑  
✅ **EdgeInfo:** 支持条件边和权重  
✅ **Swarm.register_agent():** 代理注册机制  
✅ **Swarm.handoffs_desc():** 代理描述生成  
✅ **agent.handoffs:** 代理移交列表  
✅ **agent.feedback_tool_result:** 代理作为工具标志

## 3. Implementation Design

### 3.1 Architecture Overview

**Hybrid 拓扑结构：**
```
                Root Agent (Orchestrator)
                    /    |    \
                   /     |     \
        Executor1  <---> Executor2 <---> Executor3
            \               |               /
             \              |              /
              \             |             /
                     Sub-orchestrator
                       /         \
                      /           \
                Worker1 <-----> Worker2
```

**关键特性：**
1. **层级结构：** Root → Executors → Workers (可多层)
2. **点对点通信：** 同层 Executors 可直接通信
3. **混合控制：** Root 监督 + Executors 协作

### 3.2 Event-Driven Architecture for Hybrid

**核心理念：利用 AWorld 的 Event 机制实现 Peer-to-Peer 通信**

AWorld 已有完整的事件驱动基础设施：

**EventManager 核心能力：**
```python
class EventManager:
    async def emit(sender, receiver, topic, data)  # 发送事件
    async def consume(nowait=False)                 # 消费事件
    async def register(event_type, topic, handler)  # 注册处理器
    async def messages_by_sender(sender, key)       # 按发送者查询
    async def messages_by_topic(topic, key)         # 按主题查询
```

**Event Types (Constants):**
- `AGENT`: 代理级事件
- `TOOL`: 工具执行事件
- `TASK`: 任务事件
- `AGENT_CALLBACK`: 代理回调
- `TOOL_CALLBACK`: 工具回调

**Topic Types:**
- `AGENT_RESULT`: 代理执行结果
- `TOOL_RESULT`: 工具执行结果
- `TASK_RESPONSE`: 任务响应

**Hybrid 架构的 Event 使用策略：**

1. **Hierarchical Communication (Root ↔ Executors):**
   - 使用现有 TeamSwarm 机制（Centralized）
   - Root 通过 handoffs 调用 Executor（agent-as-tool）
   - Event Type: `AGENT`
   - Topic: `AGENT_RESULT`

2. **Peer-to-Peer Communication (Executor ↔ Executor):**
   - **NEW:** 通过 EventManager 实现同层代理间通信
   - Event Type: `AGENT` (peer request/response)
   - Custom Topics: 
     - `PEER_REQUEST`: Executor 向 peer 请求协作
     - `PEER_RESPONSE`: Peer 返回协作结果
     - `PEER_BROADCAST`: Executor 广播信息给同层所有 peers
   
   **Event Flow:**
   ```
   Executor1 (sender)
       ↓ emit(event_type=AGENT, topic=PEER_REQUEST, receiver=Executor2)
       ↓
   EventBus (async routing)
       ↓
   Executor2 (receiver)
       ↓ consume() → process → emit(topic=PEER_RESPONSE)
       ↓
   Executor1 (consume response)
   ```

3. **Event Subscription Pattern:**
   ```python
   # Executor 注册 peer handler
   async def handle_peer_request(msg: Message):
       peer_request = msg.payload
       result = await process_request(peer_request)
       await context.event_manager.emit(
           data=result,
           sender=self.id(),
           receiver=msg.sender,
           topic=TopicType.PEER_RESPONSE,
           event_type=Constants.AGENT
       )
   
   # 在 Executor 初始化时注册
   await context.event_manager.register(
       event_type=Constants.AGENT,
       topic="PEER_REQUEST",
       handler=handle_peer_request
   )
   ```

4. **Advantages of Event-Driven Peer Communication:**
   - ✅ **Asynchronous:** 非阻塞，支持并发 peer 请求
   - ✅ **Decoupled:** Executor 不需要直接持有 peer 引用
   - ✅ **Traceable:** 所有 peer 通信可通过 `messages_by_topic` 追溯
   - ✅ **Scalable:** 添加新 peer 无需修改现有代理代码
   - ✅ **Observable:** 支持监控 peer 通信模式和性能

### 3.3 Core Components

#### 3.3.1 GraphBuildType Extension

```python
class GraphBuildType(Enum):
    WORKFLOW = "workflow"
    HANDOFF = "handoff"
    TEAM = "team"
    HYBRID = "hybrid"  # NEW
```

#### 3.2.2 HybridBuilder Implementation

**核心逻辑：**
```python
class HybridBuilder(TopologyBuilder):
    """Hybrid mechanism combining hierarchical oversight and peer-to-peer coordination.
    
    Supports three construction patterns:
    
    1. Explicit Hierarchy + Peer edges:
       >>> root = Agent(name='orchestrator')
       >>> exec1, exec2, exec3 = Agent('e1'), Agent('e2'), Agent('e3')
       >>> Swarm(
       >>>     topology=[
       >>>         (root, [exec1, exec2, exec3]),  # Hierarchical edges
       >>>         (exec1, exec2), (exec2, exec3)  # Peer-to-peer edges
       >>>     ],
       >>>     root_agent=root,
       >>>     build_type=GraphBuildType.HYBRID
       >>> )
    
    2. Layered Definition:
       >>> root = Agent(name='orchestrator')
       >>> layer1 = [exec1, exec2, exec3]
       >>> layer2 = [worker1, worker2]
       >>> Swarm(
       >>>     topology=[root, layer1, layer2],
       >>>     peer_connections={
       >>>         'layer1': [(exec1, exec2), (exec2, exec3)],
       >>>         'layer2': [(worker1, worker2)]
       >>>     },
       >>>     build_type=GraphBuildType.HYBRID
       >>> )
    
    3. Auto-detection from TeamSwarm:
       >>> # TeamSwarm with peer_handoffs automatically becomes Hybrid
       >>> team = TeamSwarm(root, exec1, exec2, exec3,
       >>>                  peer_handoffs=[(exec1, exec2)])
    """
    
    def __init__(self, topology, root_agent, peer_connections=None, **kwargs):
        super().__init__(topology, root_agent, **kwargs)
        self.peer_connections = peer_connections or []
        self.layers = self._detect_layers()
    
    def _detect_layers(self) -> List[List[BaseAgent]]:
        """Detect hierarchical layers from topology structure."""
        # BFS-based layer detection
        pass
    
    def _validate_peer_connections(self) -> bool:
        """Validate that peer connections are within same layer."""
        pass
    
    def build(self) -> AgentGraph:
        """Build hybrid graph with hierarchical + peer edges."""
        agent_graph = AgentGraph(GraphBuildType.HYBRID.value, root_agent=self.root_agent)
        
        # Step 1: Build hierarchical structure (like TeamBuilder)
        # Step 2: Add peer-to-peer edges (like HandoffBuilder)
        # Step 3: Validate no cross-layer peer edges
        # Step 4: Set up bidirectional handoffs for peers
        
        return agent_graph
```

#### 3.2.3 HybridSwarm Class

```python
class HybridSwarm(Swarm):
    """Hybrid paradigm: Hierarchical oversight + peer-to-peer coordination.
    
    Key Features:
    - Inherits Centralized architecture from TeamSwarm
    - Adds Event-driven peer-to-peer communication
    - Maintains hierarchical control (root → executors)
    - Enables executor collaboration without root bottleneck
    """
    
    def __init__(self,
                 *args,
                 topology: List[tuple] = None,
                 root_agent: BaseAgent = None,
                 peer_connections: List[tuple] = None,
                 max_steps: int = 0,
                 register_agents: List[BaseAgent] = None,
                 builder_cls: str = None,
                 event_driven: bool = True,  # MUST be True for Hybrid
                 **kwargs):
        super().__init__(*args,
                         topology=topology,
                         root_agent=root_agent,
                         max_steps=max_steps,
                         register_agents=register_agents,
                         build_type=GraphBuildType.HYBRID,
                         builder_cls=builder_cls,
                         event_driven=event_driven,
                         **kwargs)
        self.peer_connections = peer_connections or []
        
        # Validate event_driven is True
        if not event_driven:
            raise ValueError("HybridSwarm requires event_driven=True for peer communication")
```

#### 3.2.4 Event-Driven Peer Communication Implementation

**Peer Agent Extension:**
```python
class PeerCapableAgent(Agent):
    """Agent extension with peer communication capabilities via EventManager."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._peer_agents: List[str] = []  # Peer agent IDs in same layer
        self._is_peer_enabled: bool = False
        self._peer_handlers_registered: bool = False
    
    async def _register_peer_handlers(self, context: Context):
        """Register event handlers for peer communication."""
        if self._peer_handlers_registered:
            return
        
        # Handler for incoming peer requests
        async def handle_peer_request(msg: Message):
            logger.info(f"{self.name()} received peer request from {msg.sender}")
            peer_request = msg.payload
            
            # Process request (could invoke tools, reason, etc.)
            result = await self._process_peer_request(peer_request)
            
            # Send response back to requesting peer
            await context.event_manager.emit(
                data=result,
                sender=self.id(),
                receiver=msg.sender,
                topic="PEER_RESPONSE",
                event_type=Constants.AGENT
            )
        
        # Handler for broadcast messages from peers
        async def handle_peer_broadcast(msg: Message):
            logger.info(f"{self.name()} received broadcast from {msg.sender}")
            broadcast_data = msg.payload
            # Update internal state based on broadcast
            await self._handle_broadcast(broadcast_data)
        
        # Register handlers
        await context.event_manager.register(
            event_type=Constants.AGENT,
            topic="PEER_REQUEST",
            handler=handle_peer_request
        )
        
        await context.event_manager.register(
            event_type=Constants.AGENT,
            topic="PEER_BROADCAST",
            handler=handle_peer_broadcast
        )
        
        self._peer_handlers_registered = True
        logger.info(f"{self.name()} peer handlers registered. Peers: {self._peer_agents}")
    
    async def request_peer_collaboration(
        self,
        peer_id: str,
        request_data: Dict[str, Any],
        context: Context,
        timeout: float = 30.0
    ) -> Any:
        """Request collaboration from a specific peer.
        
        Args:
            peer_id: Target peer agent ID
            request_data: Data to send to peer
            context: Execution context
            timeout: Max wait time for response
        
        Returns:
            Response from peer
        """
        if not self._is_peer_enabled:
            raise RuntimeError(f"{self.name()} is not peer-enabled")
        
        if peer_id not in self._peer_agents:
            raise ValueError(f"{peer_id} is not in peer list: {self._peer_agents}")
        
        # Send request
        await context.event_manager.emit(
            data=request_data,
            sender=self.id(),
            receiver=peer_id,
            topic="PEER_REQUEST",
            event_type=Constants.AGENT
        )
        
        # Wait for response (with timeout)
        start_time = time.time()
        while time.time() - start_time < timeout:
            msgs = await context.event_manager.messages_by_topic(
                topic="PEER_RESPONSE",
                key=context.task_id
            )
            
            # Find response from target peer to this agent
            for msg in reversed(msgs):
                if msg.sender == peer_id and msg.receiver == self.id():
                    return msg.payload
            
            await asyncio.sleep(0.1)
        
        raise TimeoutError(f"Peer {peer_id} did not respond within {timeout}s")
    
    async def broadcast_to_peers(
        self,
        broadcast_data: Dict[str, Any],
        context: Context
    ):
        """Broadcast information to all peers in the same layer."""
        if not self._is_peer_enabled:
            raise RuntimeError(f"{self.name()} is not peer-enabled")
        
        for peer_id in self._peer_agents:
            await context.event_manager.emit(
                data=broadcast_data,
                sender=self.id(),
                receiver=peer_id,
                topic="PEER_BROADCAST",
                event_type=Constants.AGENT
            )
        
        logger.info(f"{self.name()} broadcast to {len(self._peer_agents)} peers")
    
    async def _process_peer_request(self, request: Dict[str, Any]) -> Any:
        """Process incoming peer request. Override in subclass."""
        # Default: just echo back
        return {"status": "processed", "request": request}
    
    async def _handle_broadcast(self, data: Dict[str, Any]):
        """Handle broadcast from peer. Override in subclass."""
        pass
```

**Runtime Integration:**
```python
# In Swarm.reset() or agent initialization
if self.build_type == GraphBuildType.HYBRID.value:
    for agent in self.agents.values():
        if hasattr(agent, '_is_peer_enabled') and agent._is_peer_enabled:
            # Wrap agent with PeerCapableAgent or inject peer methods
            if not isinstance(agent, PeerCapableAgent):
                # Dynamically add peer capabilities
                agent.__class__ = type(
                    agent.__class__.__name__,
                    (PeerCapableAgent, agent.__class__),
                    {}
                )
            
            # Register handlers when context becomes available
            # This happens in the runner when agent.step() is called
```

### 3.4 Developer-Friendly Peer API

**设计理念：隐藏复杂性，提供简洁 API**

开发者在编写 agent 时应该能够直接使用 peer-to-peer coordination，无需了解底层 EventManager 细节。

#### 3.4.1 High-Level Peer API

**在 BaseAgent 中添加的 Peer 方法：**

```python
class BaseAgent:
    """Base agent with built-in peer coordination support."""
    
    # ============ Peer Communication API ============
    
    async def ask_peer(
        self,
        peer_name: str,
        question: str,
        context: Any = None,
        timeout: float = 30.0
    ) -> str:
        """向同层 peer agent 提问并获取回答。
        
        Example:
            >>> # 在 agent 的 step() 方法中
            >>> financial_analysis = await self.ask_peer(
            >>>     peer_name="FinancialAnalyst",
            >>>     question="What's the revenue trend for Q3?",
            >>>     context={"quarter": "Q3", "year": 2026}
            >>> )
        
        Args:
            peer_name: Peer agent 的名称
            question: 要提问的问题
            context: 额外的上下文信息
            timeout: 超时时间（秒）
        
        Returns:
            Peer agent 的回答字符串
        """
        if not self._is_peer_enabled:
            raise RuntimeError(
                f"{self.name()} is not in a Hybrid swarm. "
                f"Peer communication only available in HybridSwarm."
            )
        
        peer_id = self._find_peer_id_by_name(peer_name)
        if not peer_id:
            raise ValueError(f"Peer agent '{peer_name}' not found in peer list")
        
        request_data = {
            "type": "question",
            "question": question,
            "context": context,
            "sender_name": self.name()
        }
        
        response = await self._request_peer_collaboration(
            peer_id=peer_id,
            request_data=request_data,
            timeout=timeout
        )
        
        return response.get("answer", "")
    
    async def share_with_peer(
        self,
        peer_name: str,
        information: Dict[str, Any]
    ) -> bool:
        """向特定 peer 分享信息（单向，不等待响应）。
        
        Example:
            >>> # Agent 发现了重要信息，分享给 peer
            >>> await self.share_with_peer(
            >>>     peer_name="DataCollector",
            >>>     information={
            >>>         "type": "anomaly_detected",
            >>>         "details": "Revenue spike in region X",
            >>>         "timestamp": datetime.now()
            >>>     }
            >>> )
        
        Args:
            peer_name: 目标 peer agent 名称
            information: 要分享的信息字典
        
        Returns:
            是否成功发送
        """
        if not self._is_peer_enabled:
            raise RuntimeError(f"{self.name()} is not peer-enabled")
        
        peer_id = self._find_peer_id_by_name(peer_name)
        if not peer_id:
            raise ValueError(f"Peer '{peer_name}' not found")
        
        await self._context.event_manager.emit(
            data={
                "type": "information_share",
                "information": information,
                "sender_name": self.name()
            },
            sender=self.id(),
            receiver=peer_id,
            topic="PEER_SHARE",
            event_type=Constants.AGENT
        )
        
        return True
    
    async def broadcast_to_all_peers(
        self,
        message: str,
        data: Dict[str, Any] = None
    ):
        """向所有同层 peer agents 广播消息。
        
        Example:
            >>> # Coordinator agent 向所有 worker agents 广播任务更新
            >>> await self.broadcast_to_all_peers(
            >>>     message="Task priority changed",
            >>>     data={"new_priority": "high", "reason": "customer escalation"}
            >>> )
        
        Args:
            message: 广播消息文本
            data: 附加数据
        """
        if not self._is_peer_enabled:
            raise RuntimeError(f"{self.name()} is not peer-enabled")
        
        broadcast_data = {
            "type": "broadcast",
            "message": message,
            "data": data or {},
            "sender_name": self.name(),
            "timestamp": datetime.now().isoformat()
        }
        
        await self._broadcast_to_peers(broadcast_data)
    
    async def request_peer_action(
        self,
        peer_name: str,
        action: str,
        parameters: Dict[str, Any] = None,
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        """请求 peer agent 执行某个动作并返回结果。
        
        Example:
            >>> # 请求 peer 执行工具调用
            >>> result = await self.request_peer_action(
            >>>     peer_name="WebSearcher",
            >>>     action="search_web",
            >>>     parameters={
            >>>         "query": "Q3 2026 market trends",
            >>>         "max_results": 10
            >>>     }
            >>> )
            >>> search_results = result.get("results")
        
        Args:
            peer_name: Peer agent 名称
            action: 要执行的动作名称
            parameters: 动作参数
            timeout: 超时时间
        
        Returns:
            动作执行结果字典
        """
        if not self._is_peer_enabled:
            raise RuntimeError(f"{self.name()} is not peer-enabled")
        
        peer_id = self._find_peer_id_by_name(peer_name)
        if not peer_id:
            raise ValueError(f"Peer '{peer_name}' not found")
        
        request_data = {
            "type": "action_request",
            "action": action,
            "parameters": parameters or {},
            "sender_name": self.name()
        }
        
        response = await self._request_peer_collaboration(
            peer_id=peer_id,
            request_data=request_data,
            timeout=timeout
        )
        
        return response
    
    def get_peer_agents(self) -> List[str]:
        """获取所有可用的 peer agent 名称列表。
        
        Example:
            >>> peers = self.get_peer_agents()
            >>> print(f"Available peers: {peers}")
            >>> # Output: Available peers: ['Analyst', 'Researcher', 'Validator']
        
        Returns:
            Peer agent 名称列表
        """
        if not self._is_peer_enabled:
            return []
        
        peer_names = []
        for peer_id in self._peer_agents:
            # Look up peer name from AgentFactory
            if peer_id in AgentFactory._agent_instance:
                peer_agent = AgentFactory._agent_instance[peer_id]
                peer_names.append(peer_agent.name())
        
        return peer_names
    
    async def wait_for_peer_message(
        self,
        from_peer: str = None,
        message_type: str = None,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """等待来自 peer 的消息（阻塞式）。
        
        Example:
            >>> # Agent 等待 coordinator 的指令
            >>> message = await self.wait_for_peer_message(
            >>>     from_peer="Coordinator",
            >>>     message_type="task_assignment"
            >>> )
            >>> task = message.get("data", {}).get("task")
        
        Args:
            from_peer: 可选，只等待特定 peer 的消息
            message_type: 可选，只等待特定类型的消息
            timeout: 超时时间
        
        Returns:
            接收到的消息字典
        """
        if not self._is_peer_enabled:
            raise RuntimeError(f"{self.name()} is not peer-enabled")
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            msgs = await self._context.event_manager.messages_by_topic(
                topic="PEER_SHARE",
                key=self._context.task_id
            )
            
            # Filter messages
            for msg in reversed(msgs):
                if msg.receiver != self.id():
                    continue
                
                if from_peer:
                    peer_id = self._find_peer_id_by_name(from_peer)
                    if msg.sender != peer_id:
                        continue
                
                payload = msg.payload
                if message_type and payload.get("type") != message_type:
                    continue
                
                # Found matching message
                return payload
            
            await asyncio.sleep(0.1)
        
        raise TimeoutError(
            f"No message received within {timeout}s "
            f"(from_peer={from_peer}, type={message_type})"
        )
    
    # ============ Peer Event Handlers (Override in Subclass) ============
    
    async def on_peer_question(
        self,
        question: str,
        context: Any,
        sender_name: str
    ) -> str:
        """处理来自 peer 的提问。子类可以覆盖此方法。
        
        Example:
            >>> class MyAgent(Agent):
            >>>     async def on_peer_question(self, question, context, sender_name):
            >>>         if "revenue" in question.lower():
            >>>             return await self._analyze_revenue(context)
            >>>         return "I don't have information on that topic."
        
        Args:
            question: Peer 的问题
            context: 问题的上下文
            sender_name: 提问的 peer 名称
        
        Returns:
            回答字符串
        """
        # Default: 返回简单响应
        return f"Received question from {sender_name}: {question}"
    
    async def on_peer_action_request(
        self,
        action: str,
        parameters: Dict[str, Any],
        sender_name: str
    ) -> Dict[str, Any]:
        """处理来自 peer 的动作请求。子类可以覆盖此方法。
        
        Example:
            >>> class SearchAgent(Agent):
            >>>     async def on_peer_action_request(self, action, parameters, sender_name):
            >>>         if action == "search_web":
            >>>             query = parameters.get("query")
            >>>             results = await self.tool_executor.execute("web_search", query=query)
            >>>             return {"status": "success", "results": results}
            >>>         return {"status": "error", "message": f"Unknown action: {action}"}
        
        Args:
            action: 请求的动作名称
            parameters: 动作参数
            sender_name: 请求的 peer 名称
        
        Returns:
            动作执行结果字典
        """
        # Default: 返回未实现
        return {
            "status": "error",
            "message": f"Action '{action}' not implemented by {self.name()}"
        }
    
    async def on_peer_share(
        self,
        information: Dict[str, Any],
        sender_name: str
    ):
        """处理来自 peer 的信息分享。子类可以覆盖此方法。
        
        Example:
            >>> class AnalystAgent(Agent):
            >>>     async def on_peer_share(self, information, sender_name):
            >>>         if information.get("type") == "anomaly_detected":
            >>>             await self._investigate_anomaly(information["details"])
            >>>             await self.log(f"Investigating anomaly reported by {sender_name}")
        
        Args:
            information: 分享的信息字典
            sender_name: 分享信息的 peer 名称
        """
        # Default: log the information
        logger.info(f"{self.name()} received info from {sender_name}: {information}")
    
    async def on_peer_broadcast(
        self,
        message: str,
        data: Dict[str, Any],
        sender_name: str
    ):
        """处理来自 peer 的广播消息。子类可以覆盖此方法。
        
        Example:
            >>> class WorkerAgent(Agent):
            >>>     async def on_peer_broadcast(self, message, data, sender_name):
            >>>         if data.get("new_priority") == "high":
            >>>             await self._adjust_priority(data)
            >>>             await self.log(f"Priority updated by {sender_name}")
        
        Args:
            message: 广播消息文本
            data: 附加数据
            sender_name: 广播发送者名称
        """
        # Default: log the broadcast
        logger.info(f"{self.name()} received broadcast from {sender_name}: {message}")
```

#### 3.4.2 使用示例

**Example 1: Financial Analysis Hybrid Swarm**

```python
class OrchestratorAgent(Agent):
    """Root agent coordinating multiple analysts."""
    
    async def step(self, input_data):
        # Hierarchical: Delegate to executor agents (via handoffs)
        revenue_analysis = await self.call_agent("RevenueAnalyst", input_data)
        cost_analysis = await self.call_agent("CostAnalyst", input_data)
        
        # Final synthesis
        return self.synthesize(revenue_analysis, cost_analysis)


class RevenueAnalyst(Agent):
    """Executor agent analyzing revenue trends."""
    
    async def step(self, input_data):
        # Do revenue analysis
        revenue_trends = await self._analyze_revenue(input_data)
        
        # Peer-to-peer: Ask peer for market context
        market_context = await self.ask_peer(
            peer_name="MarketAnalyst",
            question="What are the market trends affecting our revenue?",
            context={"period": input_data.get("period")}
        )
        
        # Combine insights
        return {
            "revenue_trends": revenue_trends,
            "market_context": market_context
        }
    
    async def on_peer_question(self, question, context, sender_name):
        """Handle questions from peer analysts."""
        if "revenue" in question.lower():
            return await self._get_revenue_insights(context)
        return "I specialize in revenue analysis only."


class MarketAnalyst(Agent):
    """Executor agent analyzing market trends."""
    
    async def step(self, input_data):
        market_analysis = await self._analyze_market(input_data)
        
        # Peer-to-peer: Share findings with all peers
        await self.broadcast_to_all_peers(
            message="Market analysis complete",
            data={"trends": market_analysis, "timestamp": datetime.now()}
        )
        
        return market_analysis
    
    async def on_peer_question(self, question, context, sender_name):
        """Respond to peer questions about market."""
        if "market" in question.lower():
            period = context.get("period", "current")
            return await self._get_market_trends(period)
        return "Please ask about market trends."


# Create Hybrid Swarm
orchestrator = OrchestratorAgent(name="Orchestrator")
revenue_analyst = RevenueAnalyst(name="RevenueAnalyst")
cost_analyst = CostAnalyst(name="CostAnalyst")
market_analyst = MarketAnalyst(name="MarketAnalyst")

hybrid_swarm = HybridSwarm(
    orchestrator,
    revenue_analyst,
    cost_analyst,
    market_analyst,
    root_agent=orchestrator,
    peer_connections=[
        (revenue_analyst, market_analyst),  # Revenue can ask Market
        (revenue_analyst, cost_analyst),    # Revenue can ask Cost
        (cost_analyst, market_analyst)      # Cost can ask Market
    ]
)
```

**Example 2: Collaborative Research Swarm**

```python
class ResearchCoordinator(Agent):
    """Coordinates research tasks."""
    
    async def step(self, research_topic):
        # Delegate to specialized researchers
        results = await self.parallel_call([
            ("LiteratureResearcher", research_topic),
            ("DataResearcher", research_topic),
            ("ExperimentDesigner", research_topic)
        ])
        
        return self.synthesize_research(results)


class LiteratureResearcher(Agent):
    """Researches academic literature."""
    
    async def step(self, topic):
        papers = await self._search_papers(topic)
        
        # Ask DataResearcher if data exists
        data_availability = await self.ask_peer(
            peer_name="DataResearcher",
            question=f"Do we have datasets for {topic}?",
            context={"topic": topic}
        )
        
        # Request experiment design suggestions
        experiment_ideas = await self.request_peer_action(
            peer_name="ExperimentDesigner",
            action="suggest_experiments",
            parameters={"papers": papers, "data": data_availability}
        )
        
        return {
            "papers": papers,
            "data_available": data_availability,
            "experiment_suggestions": experiment_ideas
        }


class DataResearcher(Agent):
    """Finds and validates datasets."""
    
    async def step(self, topic):
        datasets = await self._find_datasets(topic)
        
        # Share dataset info with all peers
        await self.share_with_peer(
            peer_name="LiteratureResearcher",
            information={"datasets": datasets, "quality_scores": self._assess_quality(datasets)}
        )
        
        await self.share_with_peer(
            peer_name="ExperimentDesigner",
            information={"datasets": datasets, "preprocessing_needs": self._check_preprocessing(datasets)}
        )
        
        return datasets
    
    async def on_peer_question(self, question, context, sender_name):
        """Answer dataset-related questions."""
        topic = context.get("topic")
        datasets = await self._find_datasets(topic)
        return f"Found {len(datasets)} datasets for {topic}"


class ExperimentDesigner(Agent):
    """Designs experiments based on research."""
    
    async def on_peer_action_request(self, action, parameters, sender_name):
        """Handle experiment design requests."""
        if action == "suggest_experiments":
            papers = parameters.get("papers", [])
            data = parameters.get("data", "")
            
            experiments = await self._design_experiments(papers, data)
            return {
                "status": "success",
                "experiments": experiments,
                "estimated_duration": self._estimate_duration(experiments)
            }
        
        return {"status": "error", "message": f"Unknown action: {action}"}
```

#### 3.4.3 API 设计原则

1. **简洁性:** 一行代码实现 peer 通信
   ```python
   answer = await self.ask_peer("ExpertAgent", "What's your opinion?")
   ```

2. **类型明确:** 区分不同的通信模式
   - `ask_peer()`: 同步请求-响应
   - `share_with_peer()`: 异步单向分享
   - `broadcast_to_all_peers()`: 广播给所有 peers
   - `request_peer_action()`: 请求执行动作

3. **可覆盖的处理器:** 子类可以定制行为
   - `on_peer_question()`: 处理问题
   - `on_peer_action_request()`: 处理动作请求
   - `on_peer_share()`: 处理信息分享
   - `on_peer_broadcast()`: 处理广播

4. **透明性:** 底层使用 EventManager，但对开发者透明
   - 开发者不需要知道 `emit()`, `consume()`, `register()`
   - 不需要知道 Topic 类型和 Event 类型
   - 框架自动处理事件路由和消息匹配

5. **可发现性:** 开发者可以查询可用的 peers
   ```python
   available_peers = self.get_peer_agents()
   ```

### 3.5 Task Property Detection (Future Enhancement)

**基于论文的预测模型：**
```python
class TaskAnalyzer:
    """Analyze task properties to recommend optimal architecture."""
    
    def analyze_task(self, task: str, tools: List[str]) -> Dict[str, Any]:
        """Analyze task characteristics.
        
        Returns:
            {
                'tool_count': int,
                'decomposability': float,  # 0-1
                'sequential_dependency': float,  # 0-1
                'recommended_architecture': GraphBuildType,
                'confidence': float
            }
        """
        pass
    
    def predict_architecture(self, properties: Dict) -> GraphBuildType:
        """Predict optimal architecture based on R² = 0.513 model."""
        # Implementation based on paper's predictive model
        pass
```

## 4. Implementation Plan

### 4.1 Phase 1: Core Hybrid Support (Current Sprint)

**Tasks:**

1. **Add HYBRID to GraphBuildType**
   - File: `aworld/core/agent/swarm.py:19-25`
   - Add: `HYBRID = "hybrid"` to enum

2. **Implement HybridBuilder**
   - File: `aworld/core/agent/swarm.py` (new class after TeamBuilder)
   - Logic:
     - Inherit from TopologyBuilder
     - Accept peer_connections parameter
     - Build hierarchical edges (parent → children)
     - Build peer edges (within same layer)
     - Validate no cross-layer peers
     - Set feedback_tool_result = True for all agents
     - Configure bidirectional handoffs for peer agents

3. **Implement HybridSwarm**
   - File: `aworld/core/agent/swarm.py` (new class after TeamSwarm)
   - Pass peer_connections to HybridBuilder
   - Maintain backward compatibility

4. **Update BUILD_CLS Registry**
   - File: `aworld/core/agent/swarm.py:1207-1211`
   - Add: `GraphBuildType.HYBRID.value: HybridBuilder`

5. **Update Documentation**
   - File: `aworld/core/agent/swarm.py:27-33` (class docstring)
   - Add Hybrid to supported topologies

### 4.2 Phase 2: Validation & Testing (BDD Approach)

**Benchmark Validation:**

Per AWorld's Benchmark-Driven Development (BDD) principle:

1. **Establish Baseline:**
   ```bash
   cd examples/gaia
   python run.py --split validation --start 0 --end 50
   # Record: Pass@1 and Pass@3 for current architectures
   ```

2. **Create Hybrid Test Agent:**
   - Identify GAIA tasks suitable for Hybrid (partially parallelizable)
   - Implement Hybrid topology for selected tasks
   - Compare: Single-Agent vs Team vs Hybrid

3. **Expected Results (Hypothesis):**
   - Tasks with sub-task independence: Hybrid ≈ Team
   - Tasks with inter-agent dependencies: Hybrid > Team
   - Sequential tasks: Hybrid < Single-Agent (accept trade-off)

4. **Validation Metrics:**
   ```
   GAIA Performance:
   - Baseline (Team): X% Pass@1, Y% Pass@3
   - Hybrid: X'% Pass@1, Y'% Pass@3
   - ΔPerformance: (X'-X), (Y'-Y)
   - Tasks affected: [list specific tasks]
   ```

**Unit Tests:**
```python
def test_hybrid_builder_hierarchical_edges():
    """Test hierarchical edge creation."""
    pass

def test_hybrid_builder_peer_edges():
    """Test peer-to-peer edge creation within layers."""
    pass

def test_hybrid_builder_cross_layer_validation():
    """Test rejection of cross-layer peer connections."""
    pass

def test_hybrid_swarm_handoffs():
    """Test bidirectional handoffs for peer agents."""
    pass
```

### 4.3 Phase 3: Advanced Features (Future)

**Not in current scope, but documented for future:**

1. **Task Property Analyzer:**
   - Implement TaskAnalyzer class
   - Integrate paper's R² = 0.513 predictive model
   - Auto-recommend architecture

2. **Error Amplification Metrics:**
   - Track error propagation across agent layers
   - Compare Hybrid vs other architectures
   - Add to benchmark reports

3. **Dynamic Architecture Selection:**
   - Runtime architecture switching
   - Based on task analysis results

4. **Performance Optimization:**
   - Adaptive peer connection topology
   - Load balancing across layers
   - Communication overhead monitoring

## 5. Architecture Comparison

### 5.1 Use Case Matrix

| Task Type | Current Best | With Hybrid | Rationale |
|-----------|--------------|-------------|-----------|
| 完全独立子任务 | Team | Team | 无需 peer 通信 |
| 部分依赖子任务 | Team (limited) | **Hybrid** | Peer 通信避免中心瓶颈 |
| 严格顺序任务 | Single-Agent | Single-Agent | 多代理无优势 |
| 高工具密度任务 | Single-Agent | Single-Agent | 协调开销过大 |
| 复杂协作推理 | Handoff | **Hybrid** | 层级 + 协作平衡控制 |

### 5.2 Performance Prediction

**Based on Paper's Findings:**

| Scenario | Expected Performance Change |
|----------|----------------------------|
| Financial Analysis (decomposable) | +5% to +10% vs Team |
| Web Navigation (mixed dependencies) | +15% to +25% vs Team |
| Planning (sequential) | -20% to -30% vs Single-Agent (acceptable) |
| Tool-heavy Coding (16+ tools) | -10% to -20% vs Single-Agent (avoid) |

## 6. Risk Assessment

### 6.1 Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Increased complexity | High | Medium | Comprehensive documentation + examples |
| Peer communication overhead | Medium | Medium | Benchmark validation + performance monitoring |
| Cross-layer edge bugs | Low | High | Strict validation in HybridBuilder |
| Backward compatibility | Low | High | Existing GraphBuildTypes unchanged |

### 6.2 Performance Risks

**From Paper's Sequential Penalty:**
- Hybrid may underperform Single-Agent on sequential tasks (39-70% degradation)
- **Mitigation:** Document use cases clearly; recommend Single-Agent for sequential tasks

**Tool-Coordination Trade-off:**
- High tool count (16+) may negate Hybrid benefits
- **Mitigation:** Add tool count warning in documentation

## 7. Success Criteria

### 7.1 Implementation Complete

- [ ] GraphBuildType.HYBRID added
- [ ] HybridBuilder implemented and tested
- [ ] HybridSwarm class implemented
- [ ] BUILD_CLS registry updated
- [ ] Documentation updated
- [ ] Example code provided

### 7.2 Validation Complete

- [ ] Unit tests pass (>95% coverage for new code)
- [ ] GAIA benchmark comparison done
- [ ] Performance report generated
- [ ] Use case guidelines documented
- [ ] No regression in existing architectures

### 7.3 Quality Metrics

- [ ] Code review approved
- [ ] Benchmark improvement documented (or acceptable trade-off explained)
- [ ] Architecture decision recorded in CLAUDE.md
- [ ] Breaking changes: None (backward compatible)

## 8. References

### 8.1 Research Paper
- **Title:** "Towards a science of scaling agent systems: When and why agent systems work"
- **Source:** Google Research Blog
- **Date:** January 28, 2026
- **Authors:** Yubin Kim, Xin Liu
- **Reference:** ArXiv preprint 2512.08296

### 8.2 Key Findings Applied
- Hybrid = Hierarchical oversight + Peer-to-peer coordination
- Alignment Principle: 80.9% improvement on parallelizable tasks (Centralized)
- Sequential Penalty: 39-70% degradation on sequential tasks (all multi-agent)
- Error Amplification: Independent (17.2x) vs Centralized (4.4x)
- Predictive Model: 87% accuracy (R² = 0.513)

### 8.3 AWorld Documentation
- CLAUDE.md: BDD principles, MAS focus area
- examples/gaia/README_GUARD.md: GAIA benchmark details
- examples/xbench/README.md: XBench multi-agent architecture

## 9. Timeline

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| Phase 1: Core Implementation | 2-3 days | Working Hybrid topology |
| Phase 2: Validation | 2-3 days | Benchmark comparison, unit tests |
| Phase 3: Documentation | 1 day | Updated docs, examples, CLAUDE.md |
| **Total** | **5-7 days** | Production-ready Hybrid support |

## 10. Next Steps

1. **Immediate:** Review this plan (current /plan-eng-review session)
2. **After Approval:** Implement Phase 1 (Core Hybrid Support)
3. **Validation:** Run GAIA benchmark with Hybrid topology
4. **Documentation:** Update all references
5. **Commit:** Following BDD commit message format with benchmark results
6. **Future:** Consider Phase 3 (Advanced Features) in subsequent sprints

---

**Document Version:** 1.0  
**Last Updated:** 2026-04-03  
**Status:** Pending Engineering Review
