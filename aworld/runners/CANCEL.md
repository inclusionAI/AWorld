## 任务取消机制（cancel）设计与使用说明

本文件记录在事件驱动 Runner 中新增的“任务取消”能力，包括变更点、核心代码与使用方式。

### 目标
- 支持两种取消方式：
  - 事件机制取消：发送 `task` 类别、`topic=__cancel` 的消息，立即终止任务。
  - 外部中心存储取消：任务状态写入中心存储（内存/Redis/SQLite 可插拔），Runner 在循环中轮询是否被取消。

---

### 变更摘要
- Task 响应扩展
  - 在 `TaskResponse` 中新增字段 `status: str | None`，标记最终状态：`success/failed/cancelled`。
- 任务事件处理
  - `DefaultTaskHandler` 在收到 `TopicType.CANCEL` 时：
    - 立即构造 `TaskResponse(status='cancelled')`；
    - 发送一个 mock 事件以快速唤醒等待；
    - 调用 `runner.stop()` 终止运行。
- 取消状态中心（可插拔存储）
  - 新增 `CancellationStore` 抽象与三种实现：
    - `InMemoryCancellationStore`（默认）；
    - `RedisCancellationStore`（需安装 redis-py）；
    - `SQLiteCancellationStore`（本地轻量 DB）。
  - 提供统一门面 `CancellationRegistry` 与工厂方法 `build_cancellation_store(conf)`。
- Runner 接入
  - `TaskEventRunner.pre_run` 读取 `task.conf['cancellation']`，通过 `build_cancellation_store` 选择后端，并将任务注册为 `running`；
  - 在主循环每次消费消息前轮询取消：优先调用自定义 `cancellation_checker`，否则查询 `CancellationRegistry`；
  - 终止时写回最终状态：依据是否取消/成功/失败设置 `TaskResponse.status` 并同步到中心存储。

---

### 关键代码摘录

1) TaskResponse 新增状态字段（路径：`aworld/core/task.py`）

```python
class TaskResponse:
    ...
    trajectory: List[Dict[str, Any]] = field(default_factory=list)
    # task final status, e.g. success/failed/cancelled
    status: str | None = field(default=None)
```

2) 事件取消处理（路径：`aworld/runners/handler/task.py`）

```python
elif topic == TopicType.CANCEL:
    # 避免阻塞，发送 mock 事件
    yield Message(session_id=self.runner.context.session_id, sender=self.name(), category='mock')
    # 标记 TaskResponse 为取消
    self.runner._task_response = TaskResponse(
        answer='', success=False, context=message.context, id=self.runner.task.id,
        time_cost=(time.time() - self.runner.start_time), usage=self.runner.context.token_usage,
        msg='cancelled', status='cancelled')
    await self.runner.stop()
```

3) 取消存储与注册表（路径：`aworld/events/cancellation.py`）

```python
class CancellationStore(abc.ABC):
    def register(self, task_id: str, status: str = TaskStatus.INIT): ...
    def set_status(self, task_id: str, status: str, reason: Optional[str] = None): ...
    def cancel(self, task_id: str, reason: Optional[str] = None): ...
    def is_cancelled(self, task_id: str) -> bool: ...
    def get(self, task_id: str) -> Optional[Dict[str, Any]]: ...

class InMemoryCancellationStore(CancellationStore): ...
class RedisCancellationStore(CancellationStore): ...
class SQLiteCancellationStore(CancellationStore): ...

class CancellationRegistry(InheritanceSingleton):
    def use_store(self, store: CancellationStore): ...
    def register(self, task_id: str, status: str = TaskStatus.INIT): ...
    def set_status(self, task_id: str, status: str, reason: Optional[str] = None): ...
    def cancel(self, task_id: str, reason: Optional[str] = None): ...
    def is_cancelled(self, task_id: str) -> bool: ...
    def get(self, task_id: str) -> Optional[Dict[str, Any]]: ...

def build_cancellation_store(conf: Optional[Dict[str, Any]]) -> CancellationStore: ...
```

4) Runner 集成（路径：`aworld/runners/event_runner.py`）

```python
# pre_run：选择后端并注册任务
cancel_conf = (self.task.conf or {}).get('cancellation')
store = build_cancellation_store(cancel_conf)
CancellationRegistry.instance().use_store(store)
...
CancellationRegistry.instance().register(self.task.id, TaskStatus.RUNNING)

# while 循环：消费前轮询取消
cancelled = False
if self._cancellation_checker and callable(self._cancellation_checker):
    cancelled = await self._maybe_await(self._cancellation_checker(self.task.id))
else:
    cancelled = CancellationRegistry.instance().is_cancelled(self.task.id)
if cancelled:
    msg = 'cancelled'
    await self.stop()
    continue

# 提供外部检查器注册
def set_cancellation_checker(self, checker: Callable[[str], Any]):
    self._cancellation_checker = checker

# 结束时：回写最终状态
if info and info.get('status') == TaskStatus.CANCELLED:
    self._task_response.status = 'cancelled'
    self._task_response.msg = self._task_response.msg or 'cancelled'
    reg.set_status(self.task.id, TaskStatus.CANCELLED)
else:
    self._task_response.status = 'success' if self._task_response.success else 'failed'
    reg.set_status(self.task.id, TaskStatus.SUCCESS if self._task_response and self._task_response.success else TaskStatus.FAILED)
```

---

### 使用说明

#### 1) 事件机制取消
- 任意处发送 `CancelMessage` 或 `task` 类别、`topic=__cancel` 的消息：

```python
from aworld.core.event.base import Message, Constants, TopicType
from aworld.core.common import TaskItem

msg = Message(
    category=Constants.TASK,
    topic=TopicType.CANCEL,
    payload=TaskItem(msg='cancel by user'),
    session_id=context.session_id,
    headers={'context': context},
)
await event_manager.emit_message(msg)
```

#### 2) 外部中心存储取消
- 配置 Runner 选择后端（在创建任务时写入 `task.conf`）：

```python
# 使用 Redis
task.conf['cancellation'] = {
    'backend': 'redis',
    'redis': { 'host': '127.0.0.1', 'port': 6379, 'db': 0, 'password': None, 'prefix': 'aworld:cancellation:' }
}

# 使用 SQLite
task.conf['cancellation'] = {
    'backend': 'sqlite',
    'sqlite': { 'file': '/tmp/aworld_cancellation.db' }
}
```

- 运行中外部随时取消：

```python
from aworld.events.cancellation import CancellationRegistry

CancellationRegistry.instance().cancel(task_id, reason='user request')
```

- 自定义检查器（例如接某业务库）：

```python
async def my_checker(task_id: str) -> bool:
    # 返回 True 表示需要取消
    return await query_something(task_id) == 'CANCELLED'

runner.set_cancellation_checker(my_checker)
```

---

### 行为与语义
- `TaskResponse.status`：
  - `cancelled`：事件取消或外部取消触发；
  - `success/failed`：由任务结果与异常决定；
  - 始终与中心存储的最终状态保持一致。
- 轮询频率：与主循环节奏一致（每次消费前检查），不会引入额外 await 间隔；如需更高实时性可结合事件取消。
- 依赖与可选项：
  - Redis 存储需安装 `redis` 包；
  - SQLite 存储默认路径 `/tmp/aworld_cancellation.db`，可通过配置修改。

---

### 兼容性与风险
- 未配置 `task.conf['cancellation']` 时默认使用内存存储，适合单进程/单机；多节点部署请使用 Redis/DB。
- 外部检查器抛出的异常会被忽略以保证主循环健壮性；如需严格失败策略，可在检查器内部自行兜底与监控。

---

### FAQ
- Q: 如何仅通过存储取消而不改动业务代码？
  - A: 直接调用 `CancellationRegistry.instance().cancel(task_id)`，Runner 会在下一次消费前检测并停止。
- Q: 任务结束后还能查询状态吗？
  - A: 可以，通过 `CancellationRegistry.instance().get(task_id)` 获取最新状态与原因。


