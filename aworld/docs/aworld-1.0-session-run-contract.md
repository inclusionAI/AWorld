# AWorld 1.0：Agent + Context + Tool 与 Session / Run

状态：2026-10-01，第一版可执行内核。预发布包 1.0.0a1，已接入文本 / function calling 的 Chat Completions 适配器并完成真实模型任务。
分支 Refactor-aworld-core，基于远端 main
`631f67f54b68251d71f8a2c5cd5b5ebced2ac6c5`。官方仓库没有 master。

## 三个核心概念

| 对象 | 职责 | 不持有的状态 |
| --- | --- | --- |
| Agent | 配置模型、Tools、Skills 与轮次上限，直接执行模型/工具 loop | 会话历史、Sandbox 管理、当前轮次 |
| Context | 唯一历史、请求视图、存储与会话工具资源 | 父 Run 准入和模型循环驱动 |
| Tool | 描述、参数 schema、校验与直接 await 的执行函数 | 调度队列或消息总线 |

Memory 不再是新内核的独立公开概念。存储属于 Context 的实现细节，摘要与检索可作为
请求策略或普通工具按需接入。当前 Context 只有历史、策略、存储与会话工具资源，不复制旧 Context 的
Task、AMNI、compiler、token 注册表和服务聚合功能。

Session / Run 是调用与观察接口：Session 绑定 Agent 和一个 Context，控制提交准入；
Run 表达一次执行。Session 的 history 直接委托 Context，没有第二份历史。
Context 身份同时作为 Session 身份；一个 Context 不能被两个 Session 同时认领。
新 Agent 由 `aworld/core/agent/loop.py` 实现，不继承旧 BaseAgent/LLMAgent。

## 入口和执行链

```python
from aworld.core.agent import Agent
from aworld.core.context import Context
from aworld.core.tool import Tool
from aworld.core.session import InMemorySessionStore, create_session, load_session

agent = Agent(model=model, tools=[tool], max_turns=20)
context = Context()
store = InMemorySessionStore()
session = await create_session(agent=agent, context=context, store=store)
run = await session.submit("执行这个任务")
result = await run.result()
session = await load_session(session.id, store=store)
```

```text
接纳 input 写入 Context
  -> 准备请求视图 -> model.complete
  -> 确认 assistant 消息
  -> 有 tool_calls：逐个 await Tool，确认 tool.result，继续 loop
  -> 无 tool_calls：返回输出，Run 提交终态
```

事件 run/model/tool/subagent.started/finished 是观察输出，不参与调用路由。
默认消息边界是 user、assistant（可带 ToolCall）、tool-result；模型适配器接收 ModelRequest，
返回 AssistantMessage。已确认的模型和工具消息写入 Context，output 是执行结果观察，
投影到模型请求时不会重复发送。当前只支持文本和完整响应；流式、多模态尚未实现。
适配器须保证已完成的调用参数，不把截断或失败响应伪装成成功结束。

ChatCompletionsModel 通过 ProviderModel 适配原 OpenAIProvider.acompletion()，默认使用 async SDK 路径。
模型通信与重试复用原 provider；CLI --max-retries 设置默认 3 次重试，原禁用重试环境变量仍生效。
适配层没有外层重试，工具不回放；不完整模型响应仍视为失败，Run 总预算和取消约束整次调用。
原 provider 与旧 Context/compiler/tokenizer/缓存/日志的耦合本轮保留，见
[Provider 复用与技术债](aworld-1.0-provider-reuse-debt.md)。

同一 Agent 可供独立 Session 使用，循环变量均为 Run 局部变量；注入的模型/工具若自身有
可变共享资源，其实现需满足宿主并发要求。历史与请求、结果、事件 payload 都进行 deepcopy 隔离。
上下文策略在每次模型请求前执行，不删除完整历史。每一轮工具调用均保留调用 ID 与对应结果。
取消中断的未完整工具轮次留在历史，后续请求视图省略该轮，不伪造已执行结果。

## 准入、取消与事件契约

create_session 自动创建 ID，可显式传 Context；不执行任务。
load_session 要求 ID 与存储，未知 ID 报错，不重放旧输入。
submit 准入后返回 RunHandle；同一 Session 最多一个非终态 Run，正忙时拒绝。
准入前输入错误直接抛出，拒绝不产生可见 Run 或历史。

| 方法 | 语义 |
| --- | --- |
| Session.context / history / snapshot / get_run | 访问 Context、已确认历史、元数据与所属执行 |
| Session.submit(input, options=None) | 接纳一次输入，返回句柄 |
| RunHandle.snapshot / result | 查询状态，读取或等待终态 |
| RunHandle.events(after_seq=0) | 回放并跟随，每个订阅者独立游标 |
| RunHandle.cancel | 幂等停止请求，result 等待清理完成 |
| Session.close | 停止活动 Run 并清理会话工具资源；保留可读历史，拒绝新提交 |

pending -> running -> completed/failed；停止先进入 cancelling，再提交 cancelled 或超时 failed。
首个停止原因胜出。取消先接纳后，即使 Agent 吞掉取消也不能变为成功。
清理期间保留执行槽，并禁止旧 RunContext 写入历史。COMPLETED 表示执行结束，不保证业务验收通过。
关闭订阅或取消等待者不取消执行；RunOptions.timeout_seconds 是固定执行预算。
Agent.max_turns 控制模型请求次数，耗尽后失败并保留已确认工具结果和部分输出。
工具运行异常转换成 tool error result，让模型有机会修正；取消继续向上传播。

每个 Run 的事件序号从 1 连续递增，最后一个唯一事件为 run.finished，携带 RunResult。
after_seq 不能为负、bool 或超出当前序号。当前保留全部事件，无过期或分布式重连承诺。

## Context 存储

默认 Context 只用标准库。ContextStorage 仅有 append/read，默认实现为内存历史。
MemoryStoreAdapter 接收显式 store 与 item_factory，不导入旧 Memory 模块；宿主可自行提供持久化存储。
桥接记录使用 message 类型、session_id 与 aworld_history 元数据命名空间，JSON 保存 run_id/kind/data。
默认历史支持可 deepcopy 的 Python payload，桥接后端要求 JSON。
旧 MemoryFactory、摘要、embedding、画像及经验提取已从内核移除。


## 原 Subagent 能力与迁移结论

工程事实：subagent_manager.py 的 spawn 创建子 Context、克隆 Agent，然后构造
Task + Swarm，通过 Runners.run_task 执行，再合并旧 Context。
spawn_subagent_tool.py 的并行依赖 Semaphore + create_task + gather，后台依赖
create_task 和任务 registry；状态同步还依赖旧 ContextVar、task_state 与 AMNI。
因此能力可以继续实现，旧执行入口和状态合并需要替换，不能直接接旧 manager。

`subagent_tools({"worker": child_agent}, max_concurrent=4)` 返回六个普通 Tool：
spawn_subagent、parallel_subagents、check_subagent、wait_subagent、cancel_subagent、list_subagents。
执行使用新的 Context / Session / Run，无 Runner 或 EventBus。

| 能力 | 新落点与已验证行为 |
| --- | --- |
| 同步调用 | spawn 默认等待；父 Run 取消会取消子任务并等待清理 |
| 并行任务 | parallel 内部 gather；同一工具集在一个 Session 中的并发由 Semaphore 限制 |
| 后台任务 | background=true 返回 task_id；可跨父 Run 查询、等待、取消 |
| 生命周期 | Session.close 停止后台子任务并等待清理；取消 wait 不停止后台任务 |
| 隔离 | 任务句柄按 Session 隔离；子 Context 保留独立历史，只回传输出与错误 |
| 物理资源 | 子 Agent 仅使用其配置的 Tools；共享 Sandbox 需在构造工具时明确指定 |

内部 registry 是 Context 的会话工具资源，不是另一份历史。后台完成不能持有父 RunContext
写入事实；check/wait 的返回值由当前父 loop 写成普通 tool.result。已完成句柄保留至会话关闭。
并发上限不是全局预算；当前没有跨层共享预算、递归深度限制或 agent.md 角色发现。

## 默认 Tool、Skills 与物理承载

Sandbox 定位为物理承载，默认 `aworld.core.sandbox.LocalSandbox`，负责本地 cwd、文件与进程。
它不导入旧 `aworld.sandbox` 的 MCP、AMNI、自动安装和环境编排。Agent 不持有 Sandbox；
`default_tools(sandbox=LocalSandbox(cwd))` 把物理操作绑定到普通 Tool。
容器/远程承载可实现相同 async read/write/bash 方法，Agent loop 不需要改变。
本地 cwd 是执行位置，绝对路径与 cwd 之外的路径可用；LocalSandbox 不提供 OS 访问隔离。
当前本地 Bash 后端要求 POSIX，使用 /bin/bash，不读取 shell profile、不修改全局 cwd。

Agent(model=...) 默认启用六个工具；显式 tools 替换默认集合，tools=[] 禁用所有默认工具。

| Tool | 参数与行为 |
| --- | --- |
| read | path, offset=1, limit=2000；UTF-8 文本，2000 行/50 KiB 上限；next_offset 续读，超长单行提示按字节读取 |
| write | path, content；完整覆盖，创建父目录，临时文件替换，保留现有文件权限 |
| bash | command, timeout=120 秒；输出末尾 2000 行/50 KiB，截断时提供完整临时日志路径 |
| read_session | session_id, offset=1, limit=50；会话元数据与确认历史，最多 200 条 |
| search_sessions | query 文本, offset=1, limit=20；大小写不敏感搜索，最多 100 个匹配 |
| session_query | query 对象, offset=1, limit=20；组合 session_ids / metadata / state / text 过滤 |

Bash 失败或超时保留输出与退出码，并标记 tool error，让模型修正。取消和超时杀死本次启动的
进程组并等待清理；主动逃离进程组的子进程不在该保证内。文件线程不能强杀，取消会等待已接纳
操作完成，避免 Run 已释放但文件仍继续写入。截断 Bash 日志由临时目录/宿主负责保留和清理。
read 只接受普通文件，避免 FIFO/设备读取阻塞。首版没有图片读取、edit/patch、后台 Bash、输出流式。

`ToolRegistry` 是显式工具集合，统一 schema、查找、参数校验和直接调用；重名拒绝。
select 返回能力子集，extend 返回新集合，均不修改原集合，不使用全局注册或动态事件路由。
自定义工具需要完整 JSON Schema 校验时提供 validate_arguments；当前没有通用 Schema 引擎。

Skill 是指令/资源配置，没有执行 loop。Agent(skills=[Skill(...)]) 绑定 inline 指令与显式 tools。
load_skills(*directories) 从明确目录发现一层 SKILL.md；只把名称、描述、路径放入 prompt，
由 read 在相关任务中加载正文；相对引用以 Skill 目录为准。显式文件加载需要 PyYAML，默认内核
无需它，不自动安装、下载、同步或运行脚本。重名及缺少 read 能力拒绝。尚未实现 /skill 命令。

## 跨会话查询范围

默认会话工具使用 create_session 绑定的 InMemorySessionStore，不扫描全局目录或机器上的历史。
多个 Session 显式共享同一个 Store，才能默认跨会话读取；未指定 Store 时各会话拥有独立 Store。
关闭的 Session 保留在 Store 中可读取。允许所有宿主会话适用于单用户受信宿主；多租户宿主应使用
独立 Store，或显式 session_tools(store, allowed_session_ids=[...]) 定义工具可见范围。
白名单外与未知 ID 均报 unavailable；仅返回复制数据，不重新执行，也不把子/目标历史合并进 Context。

```json
{"query": {"metadata": {"project": "aworld"}, "state": "idle", "text": "上下文"}, "limit": 20}
```

session_query 所有过滤条件为 AND；metadata 按指定键精确匹配；state 是 active/idle。
空 query 列出可见会话。返回 matches、total_matches、next_offset；每项带 session_id、metadata、
active_run_id、matched_offset、matched_kind、snippet。用 read_session 获取详情。
search_sessions 是 query.text 的简写，搜索历史、元数据和 ID。
当前扫描进程内历史，无索引或语义检索；分页读取活动数据，不承诺跨页快照一致，也不提供重启恢复。

## 外部事实与取舍

[Pi loop 源码](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent-loop.ts)
用 runLoop 控制模型调用、工具执行和下一轮，事件流报告运行过程；其实际实现有
steering、follow-up、工具并行等扩展，所以单 loop 指控制权集中，不是字面只有一个 while。
[Hermes conversation_loop](https://github.com/NousResearch/hermes-agent/blob/main/agent/conversation_loop.py)
用迭代循环依次准备请求、调用模型、处理响应并运行工具轮；外围还有重试、压缩和会话管理。
[Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)
将历史存储与会话执行分开。AWorld 本次采用三核心概念，存储收归 Context。

[Pi read/write/bash](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/src/core/tools) 提供 cwd 绑定、可替换 operations、读取分页与 Bash 输出截断/取消。
[Hermes file tools](https://github.com/NousResearch/hermes-agent/blob/main/tools/file_tools.py) 与 [terminal 环境](https://github.com/NousResearch/hermes-agent/blob/main/tools/terminal_tool.py) 也把文件操作与物理环境对应起来，默认环境为 local。
[Pi Skills](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md) 采用目录声明与按需加载正文；本版保留这项最小机制，没有照搬包分发或自动扫描全局目录。

取舍：直接 loop 易于追踪、测试、取消和局部组合；需要跨进程持久调度时可在宿主层增加。
已有 LLMAgent 约 5550 行，混合 MemoryFactory、请求准备、compiler、预算、工具执行和输出事件；
本次从零写最小 Agent，没有逐方法搬运它的复杂链路，也没有为旧入口做兼容层。

## 运行与验证

默认与文件存储 demo 均完成 5 -> 9，每次两次模型请求和一次真实工具调用。
完整 Context 为十项历史，文件后端重开可读回十项。tools_demo 实际执行 write/read/bash，并通过 session_query/read_session 读取另一个会话的七条历史。
Python 3.10 标准库内核 53 项检查（52 通过，1 项可选 YAML 文件加载跳过）；Python 3.12 核心、存储与 ModelResponse usage 回归共 77 项通过。
检查覆盖单轮与工具续轮、逐请求策略、错误反馈、重复调用拒绝、轮次预算、取消残留轮次、
父子隔离、取消清理、有界并行与后台生命周期、本地文件/进程操作、Skill 加载、跨会话查询及可见范围。
stdlib-only 子进程实际运行默认 Agent + inline Skill，确认不加载旧 Sandbox、Skills、YAML、EventBus/Runner/LLMAgent/Memory/AMNI/compiler/tokenizer。
未运行真实模型 API或依赖外部服务的全仓测试；当前边界为本地核心与存储回归。
