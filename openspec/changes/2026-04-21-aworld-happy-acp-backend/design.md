## Context

现有 AWorld gateway/channel 体系更偏向“外部消息入口适配”：

- `InboundEnvelope -> GatewayRouter -> OutboundEnvelope`
- 适合 DingTalk、Telegram 这类 message channel
- 重点是入口路由与最终回复，而不是长期运行的 agent session host

Happy 的体系边界则不同：

- App/Web 是端侧 UI
- Happy Server 负责账号、加密会话、实时同步与多端状态
- Happy CLI/daemon 运行在 worker host 上，负责拉起/控制本机 agent backend

因此，如果不修改 Happy，AWorld 不应要求 Happy 适配新的 AWorld channel；反之，AWorld 需要暴露 Happy 已经理解的 backend 接口。

在现有 Happy 代码中，ACP-compatible backend 是最清晰、最弱耦合的接入形态。相比把 AWorld 伪装成 OpenClaw 网关或继续扩张 gateway channel 模型，ACP 方案有三个优势：

- Happy 侧已有 generic ACP runner，协议心智和接入面更直接。
- AWorld 可以把改动收敛在 `aworld-cli` 宿主层，而不是下沉到 `aworld/core`。
- 同机与分布式部署都天然成立，因为 Happy Server 只负责控制面和同步面，真正执行 AWorld 的是 worker host 上的 Happy CLI/daemon + AWorld backend。

## Goals / Non-Goals

**Goals**

- 让 Happy 现有体系能够把 AWorld 作为一个可远程控制的 agent backend 使用。
- 保持 AWorld Agent SDK/framework 内核不受 Happy 接入需求侵入。
- 支持同机与分布式两种部署形态。
- 在不修改 Happy 代码的前提下，保留 Happy 既有能力面，而不是只保留文本聊天主链路。
- 为第一阶段实现定义足够小的 ACP 事件子集，先跑通端侧控制主链路，但不把阶段性切片误写成最终边界。
- 用 OpenSpec 把当前共识与边界固定下来，供后续继续深化设计。

**Non-Goals**

- 不新增 `aworld_gateway/channels/happy` 作为当前 gateway message channel 模型的一部分。
- 不要求 Happy App、Happy Server、Happy CLI 做任何代码修改。
- 不在本 change 中承诺一比一暴露 AWorld 全部 runtime 内部状态。
- 不在本 change 中承诺第一阶段就覆盖 artifact/file 富展示、完整 subagent 可视化、复杂审批 UI 语义。
- 不在本 change 中把 Happy 现有语音等能力从最终目标中删掉；这里只是不在第一阶段把所有能力一次性落完。

## Decisions

### Decision: AWorld uses an ACP-compatible backend instead of a new Happy channel

首选实现方向是 AWorld 提供一个 ACP-compatible backend，由 Happy 现有 ACP runner 拉起与控制。

Why:

- Happy 已有接入面可用，无需改 Happy。
- channel 模型要求 Happy 作为平台入口适配器接入 AWorld；这与当前实际链路不符。
- ACP 更适合“本机 agent backend 被宿主进程控制”的关系。

Rejected alternatives:

- `aworld_gateway/channels/happy`
  Rejected because Happy 不会主动调用新的 AWorld channel 抽象。
- OpenClaw-compatible gateway host as primary path
  Rejected for now because它会把 AWorld 绑定到更专有的 gateway 协议与服务化形态，复杂度高于 ACP。

### Decision: The implementation boundary stays in aworld-cli and gateway-adjacent host code

首版实现边界固定为：

- 可以修改 `aworld-cli`
- 可以在 gateway/host 辅助层增加新模块
- 不修改 `aworld/core`

Why:

- `aworld/core` 应保持为 AWorld Agent SDK/framework 内核。
- Happy 接入需求属于宿主层与协议适配层问题。
- 当前 CLI/gateway 外层已经能包装 `LocalAgentExecutor` 与现有运行时输出。

### Decision: Happy CLI/daemon is the required host role, but AWorld integrates only against the generic ACP contract

在当前约束下，`Happy CLI/daemon` 作为 host role 是必选集成边界。

这里的“必选”指的是职责边界，而不是要求 AWorld 依赖 Happy 的私有 agent 实现：

- Happy CLI/daemon 负责在 worker host 上拉起 backend 进程
- Happy CLI/daemon 负责持有本机 cwd / env / session 控制上下文
- Happy CLI/daemon 负责把 Happy 控制面流量转成对 backend 的 ACP 调用

但 AWorld 只对接其 generic ACP backend contract，不依赖以下内容：

- Happy 私有 agent runner 实现
- Happy 内部 provider-specific transport 细节
- Happy 特定 agent 类型的私有消息格式

Why:

- 这样才能满足“不改 Happy 代码”的约束
- 这样才能避免 AWorld 被 Happy 内部 agent 实现细节耦死
- 这样也保留了 Happy 后续在自身内部替换/扩展 agent 实现的空间，而不会影响 AWorld 作为 ACP backend 的接入方式

### Decision: AWorld ACP is a generic capability; Happy is the current validation target, not a naming anchor

虽然当前方案围绕 Happy 体系展开，但 AWorld 侧新增的 ACP 能力本身应被视为泛化能力，而不是“为 Happy 定制的 ACP”。

这条约束直接影响代码组织：

- 目录名、模块名、命令名应使用 `acp`、`backend`、`session`、`host` 这类泛化命名
- 不应引入 `happy_acp_*`、`happy_backend_*`、`for_happy_*` 之类把 Happy 固化进 AWorld 公共实现面的命名
- 若需要表达“这是用于 Happy 验证的适配/测试”，应放在测试、文档、示例或验证层，而不是主实现命名空间

Why:

- AWorld 扩展 ACP 的目标不是只服务 Happy 一个宿主
- 如果把 Happy 语义写进主实现命名，后续泛化和复用成本会显著上升
- 用户已经明确要求：Happy 只是当前对接与验证体系，不应成为 AWorld ACP 能力的产品命名锚点

### Decision: ACP implementation should be isolated in a unified aworld-cli directory with a thin CLI integration seam

为了控制回归风险，AWorld ACP 的实现应尽量收敛在 `aworld-cli` 下单独的统一目录，而不是把逻辑分散改进现有各个 CLI 子系统。

建议原则：

- 主体实现集中在 `aworld_cli/acp/`
- 现有 `main.py`、命令解析、runtime/executor 只做最薄接线
- 尽量不修改现有 `gateway`、`protocal/http.py`、`protocal/mcp.py`、普通交互式 CLI 流程

Why:

- 这样可以把 ACP 相关回归面压到最小
- 这样后续验证可以聚焦 `tests/acp/` 和少量集成测试
- 这样避免因为引入 ACP 而触发大量与现有 CLI 无关的回归测试成本

### Decision: Happy Server and AWorld worker are deployment-separable

方案必须同时支持两种部署形态：

- 同机部署：
  - Happy Server
  - Happy CLI/daemon
  - AWorld backend
  - AWorld workspace
  在同一 host
- 分布式部署：
  - Happy Server 单独部署
  - AWorld worker host 单独运行 Happy CLI/daemon 与 AWorld backend

Why:

- Happy Server 只负责控制面、同步与加密消息中继。
- AWorld 应部署在真正持有 workspace、工具链与执行环境的 worker host 上。

### Decision: The end-state keeps Happy capability parity, while phase 1 only slices the first ACP surface

最终目标不是“做一个文本 ACP backend”。

最终目标是：AWorld 接入后，Happy 既有端侧能力仍可继续使用，AWorld 只是替换/新增 backend 能力承载，而不是削掉 Happy 原有能力面。

这意味着：

- Happy 的语音能力仍然应由 Happy 自己的 app/server/voice pipeline 负责
- AWorld backend 不应要求 Happy 为了适配 AWorld 而改动 voice 流程
- 若 Happy 的语音、富交互或其它端侧能力最终需要某些 backend 配合能力，这些能力应在 AWorld 侧逐步补齐，而不是反过来要求裁剪 Happy

在这个最终方向下，第一阶段只先覆盖 Happy 端侧控制主链路所需的最小 ACP 事件子集：

- `initialize`
- `newSession`
- `prompt`
- `cancel`
- `agent_message_chunk`
- optional `agent_thought_chunk`
- `tool_call`
- `tool_call_update`

Deferred from phase 1 but still part of the end-state discussion:

- `loadSession`
- `listSessions`
- `setSessionMode` / config/model 相关扩展
- artifact/file 富展示
- subagent 与 background task 的专门协议表达
- 更复杂的 approval / permission 交互
- Happy 原生语音等能力与 AWorld backend 的完整对齐
- 使用率、workspace、summary 等 richer metadata

### Decision: Gateway code may be reused as host infrastructure, but not as the primary abstraction

现有 `aworld-cli gateway server` 可以复用其宿主能力，例如：

- agent loading
- HTTP app
- artifact service
- runtime health/status surface

但它不应作为 Happy 方案的主抽象边界，因为当前 `GatewayRouter`/`ChannelAdapter` 模型面向的是 message channel，而不是 stateful backend host。

## Deployment Model

### Topology A: Same-host deployment

```text
Happy App/Web
    |
Happy Server
    |
Happy CLI / daemon
    |
AWorld ACP backend
    |
AWorld agent + local workspace/tools
```

说明：

- Happy Server 与 AWorld backend 在同一 host 部署。
- Happy CLI/daemon 作为该 host 上的宿主进程拉起 AWorld backend。

### Topology B: Distributed deployment

```text
Happy App/Web
    |
Happy Server (host A)
    |
Happy CLI / daemon (host B)
    |
AWorld ACP backend (host B)
    |
AWorld agent + local workspace/tools (host B)
```

说明：

- Happy Server 与 AWorld worker 解耦部署。
- AWorld backend 只需与 worker host 上的 Happy CLI/daemon 协作，不需要直接绑定 Happy Server 进程。

## AWorld-side Scope

首版 AWorld 侧需要定义三层内容，但都保持在 `aworld-cli` / host 层：

### 1. Backend entrypoint

新增一个可被 Happy ACP runner 拉起的入口，例如：

- `aworld-cli acp`
- 或等价的 app-server / ACP host 命令

职责：

- 建立 ACP 会话
- 接收 prompt
- 管理 turn 生命周期
- 暴露取消/中止能力

### 2. Runtime-to-ACP event mapper

把现有 AWorld CLI/executor 输出映射成 Happy 可消费的 ACP/backend 事件：

- status running / idle / error
- text delta
- thinking
- tool-call start/end
- turn-end

该层不要求改变 `aworld/core`，而是消费 `aworld-cli` 现有 executor/streaming 输出。

### 3. Host-local session controller

负责：

- 会话级状态跟踪
- prompt 排队与 turn 串行化
- cancel / abort 协调
- 与 Happy 宿主进程的输入输出对接

## Proposed Generic Module Layout

为了落实“ACP 是泛化能力，不以 Happy 命名”为原则，建议先把未来代码组织边界写清楚。

### Recommended package shape

建议在 `aworld-cli/src/aworld_cli/` 下增加独立的 `acp/` 包，而不是把实现塞进 `gateway` 或某个 `happy_*` 命名空间。

推荐结构：

```text
aworld-cli/src/aworld_cli/
  acp/
    __init__.py
    server.py             # ACP stdio host entry / connection bootstrap
    session_store.py      # ACP session -> aworld session mapping
    turn_controller.py    # per-session active turn control / busy / cancel
    event_mapper.py       # runtime outputs -> ACP updates
    runtime_adapter.py    # wraps LocalAgentExecutor / streamed_run_task
    errors.py             # protocol-facing error normalization
    debug_client.py       # local self-validation harness (optional but recommended)
  commands/
    acp.py                # CLI command binding if command parsing is split out
```

为什么建议单独 `acp/` 包：

- 它与现有 `gateway` 的 message-channel 抽象不同
- 它与现有 `protocal/http.py`、`protocal/mcp.py` 的职责也不完全相同
- 它未来可能需要自己的一套 session/turn/control 语义，因此独立目录更稳
- 它也满足“尽量不扰动原有 aworld-cli 功能”的实现约束

### Command surface recommendation

建议公共命令面保持纯泛化命名：

- `aworld-cli acp`
- 可选子命令：
  - `aworld-cli acp doctor`
  - `aworld-cli acp self-test`

不建议：

- `aworld-cli happy-acp`
- `aworld-cli happy-backend`
- `aworld-cli acp-happy`

### Responsibility split inside the ACP package

为了避免后续实现再次耦到 Happy 或 CLI 终端渲染逻辑，建议职责边界固定如下：

- `server.py`
  - 只负责 ACP transport / stdio bootstrap / request dispatch
  - 不直接理解 AWorld runtime 内部细节
- `session_store.py`
  - 只负责 session record 生命周期
  - 不负责 turn 执行
- `turn_controller.py`
  - 负责 active turn、busy rejection、cancel、terminal resolution
  - 不负责把 runtime 输出翻译成 ACP update
- `runtime_adapter.py`
  - 负责调用 `LocalAgentExecutor` / `Runners.streamed_run_task(...)`
  - 向外暴露更稳定、可映射的运行时事件流
- `event_mapper.py`
  - 只负责把 runtime event 规范化成 ACP-facing updates
  - 不直接操作 session store

这样做的收益：

- 后续如果 Happy 之外的宿主也要接 AWorld ACP，主实现无需改命名和主结构
- 测试可以按模块边界拆开
- 将来要补 session resume、artifact bridge、approval 语义时也更容易扩展

## Plugin And Hook Leverage Strategy

在“ACP 主逻辑集中收敛”这个前提下，优先复用 AWorld 已有的 plugins / hooks 扩展能力会更稳。

`aworld_hud` 是一个很好的参考样本：

- 它作为 `builtin_plugins/aworld_hud/` 下的独立插件存在
- 通过 `.aworld-plugin/plugin.json` 暴露 entrypoints
- 主流程只暴露少量稳定状态与 hook 事件
- HUD 的动态行为通过 plugin capability 和 hooks 注入，而不是把 HUD 逻辑散落进整个 CLI

ACP 可以借鉴的不是 HUD 功能本身，而是这种扩展模式：

- 核心 host / session / turn / event mapping 留在 `aworld_cli/acp/`
- 非核心增强能力优先挂在现有扩展点上
- 主流程只增加必要的薄状态面和 hook 触发点

### Recommended split: core vs extension

建议把 ACP 相关能力分成两层。

#### Core ACP path

必须内建在 `aworld_cli/acp/` 中的内容：

- stdio ACP host
- session store
- turn controller
- runtime adapter
- event mapper
- protocol-facing error handling

这些部分不适合插件化，因为它们构成了 ACP host 的最小正确性边界。

#### Extension-friendly ACP surfaces

优先考虑通过 plugins / hooks 扩展的内容：

- session start augmentation
- prompt preprocessing / contextual injection
- tool-call observation / telemetry enrichment
- artifact publication helpers
- supplemental status surfaces for local debugging

这些部分可以借助现有插件和 hooks 机制，避免把“某个宿主集成需要的增强逻辑”写死在 ACP 主干里。

### Hook usage recommendation

结合现有 hooks 设计草案，ACP 方案优先关注以下类型的 hook 点，而不是新造一套 ACP 专用扩展系统：

- `session_start`
  - 适合会话初始化时补充上下文、建立本地辅助状态
- `user_prompt_submit`
  - 适合 prompt 进入 runtime 前做轻量预处理
- `pre_tool_use` / `post_tool_use`
  - 适合工具事件观察、附加遥测、artifact 发现

重要边界：

- hooks 用来增强 runtime 行为，不应接管 ACP protocol 正确性
- plugin / hook 执行失败不能破坏 ACP host 的主链路
- 如果某个能力只有通过插件才能成立，则说明它不应归类为 ACP core

### Plugin capability recommendation

如果后续需要为 ACP 增加“可选增强能力”，优先考虑仿照 `aworld_hud` 的能力声明方式，而不是在 ACP 主包内继续膨胀条件分支。

可考虑的方向：

- `acp_observers`
  - 订阅标准化后的 runtime / session 事件，做观测或调试增强
- `acp_artifacts`
  - 处理本地结果到 artifact surface 的桥接
- `acp_session_context`
  - 为 session start / prompt submit 提供上下文补充

这里仍要控制范围：

- 这些是后续扩展方向，不是要求现在设计新的完整 plugin capability 体系
- 当前要锁定的是“优先复用现有 plugin/hook 模式”，而不是“为 ACP 发明一套新插件系统”

### Why aworld_hud is the right reference

`aworld_hud` 证明了三件事：

- AWorld CLI 已经有内置插件的标准组织方式
- 主流程可以只暴露很薄的状态接口，插件自己消费并渲染
- 这种模式能把新增能力的回归面控制在很小范围

这与 ACP 方案的目标完全一致：

- 核心能力收敛
- 命名泛化
- 尽量少改现有 CLI 主干

## Core vs Extension Decision Table

为了避免实现阶段反复争论“这个能力该不该进 ACP core”，先给出一张明确判定表。

### Decision rule

一个能力如果满足以下任一条件，应归入 ACP core：

- 直接决定 ACP protocol 正确性
- 直接决定 session / turn 正确性
- 是所有宿主都需要的通用 backend 能力
- 失败后会导致 Happy 或其他宿主无法把 AWorld 当成可用 ACP backend

一个能力如果更接近以下特征，应优先考虑 plugin / hook：

- 主要是增强而不是协议正确性前提
- 依赖具体宿主体验或具体团队偏好
- 可以失败但不影响 ACP 主链路成立
- 更适合作为上下文注入、观察、后处理或展示增强

### Capability matrix

| Capability | Recommended layer | Why |
| --- | --- | --- |
| `stdio` ACP host bootstrap | ACP core | 属于协议入口本身 |
| `initialize/newSession/prompt/cancel` request handling | ACP core | 属于最小协议正确性 |
| ACP session store | ACP core | 属于 session 正确性 |
| active turn / busy / cancel controller | ACP core | 属于 turn 正确性 |
| runtime adapter over `LocalAgentExecutor` | ACP core | 属于通用 backend 执行桥接 |
| runtime event -> ACP update mapping | ACP core | 属于协议输出正确性 |
| final-text fallback | ACP core | 否则 Happy 可能看不到正文 |
| stable `toolCallId` management | ACP core | 否则 tool 状态无法闭合 |
| startup / protocol error normalization | ACP core | 属于可诊断性与主链路健壮性 |
| session-start context augmentation | plugin / hook preferred | 属于增强，不是协议前提 |
| prompt preprocessing / prompt decoration | plugin / hook preferred | 适合 `user_prompt_submit` 类扩展点 |
| tool-call telemetry enrichment | plugin / hook preferred | 适合 `pre_tool_use` / `post_tool_use` |
| artifact detection and publication helpers | plugin / hook preferred | 常带宿主/部署差异，适合可选增强 |
| local ACP debug HUD / status lines | plugin preferred | 类似 `aworld_hud`，不应侵入 core |
| host-specific validation helpers | validation layer | 不应进入主实现 |
| Happy-specific smoke fixtures | validation layer | 明确属于验证，而非泛化实现 |
| permission / approval orchestration | defer, likely mixed | 最终可能部分进 core、部分走扩展，但当前不应先做大 |
| subagent/background-task visualization | defer, likely mixed | 最终可能需要 core 事件面，但当前先不展开 |
| voice provider integration | not in AWorld ACP scope | 属于 Happy 原生能力，不应搬进 AWorld |

### Default bias

如果实现阶段拿不准，一律先按以下顺序判断：

1. 能否不做这项能力而仍保持 ACP host 正确可用？
2. 如果能，优先不要放进 ACP core。
3. 能否通过现有 plugin / hook 扩展点表达？
4. 如果能，优先走 plugin / hook。
5. 只有当它直接关系到协议、session、turn 正确性时，才进入 ACP core。

### Specific guidance for current phases

对当前阶段，建议直接按下面执行：

- Stage 1:
  - 只实现表中 ACP core 的必要子集
  - 明确不把增强能力提前塞进 core
- Stage 2:
  - 优先从 plugin / hook 层补 session/context/artifact 类增强
- Stage 3:
  - 再评估 approval、subagent、voice-adjacent coordination 这类 mixed 能力是否需要扩 core 事件面

## Near-term Capability Inventory

为了把“继续深化”变成后续实现前可直接使用的裁剪清单，这里把近期最可能进入实现计划的能力逐项列出。

### First-phase candidate set

| Capability | Target phase | Recommended layer | Notes |
| --- | --- | --- | --- |
| `aworld-cli acp` command entry | Stage 1 | ACP core | 公共命令面，必须泛化命名 |
| ACP stdio bootstrap | Stage 1 | ACP core | `stdin/stdout` protocol, `stderr` diagnostics |
| `initialize` request handling | Stage 1 | ACP core | 最小协议面 |
| `newSession` request handling | Stage 1 | ACP core | 先创建隔离 session |
| `prompt` request handling | Stage 1 | ACP core | 一次 prompt = 一次 turn |
| `cancel` request handling | Stage 1 | ACP core | best-effort cancel |
| host-local session record | Stage 1 | ACP core | `acp_session_id -> aworld_session_id` |
| active turn / busy rejection | Stage 1 | ACP core | 单 session 单 active turn |
| runtime adapter | Stage 1 | ACP core | 包装 `LocalAgentExecutor` / streaming outputs |
| text chunk mapping | Stage 1 | ACP core | `agent_message_chunk` |
| tool start/end mapping | Stage 1 | ACP core | `tool_call` / `tool_call_update` |
| final-text fallback | Stage 1 | ACP core | 无 chunk 时补正文 |
| stable `toolCallId` policy | Stage 1 | ACP core | tool 状态闭合前提 |
| ACP self-test / debug harness | Stage 1 | validation layer | 推荐尽早有，便于独立验证 |
| Happy same-host smoke | Stage 1 | validation layer | 证明 host contract 接通 |
| Happy distributed smoke | Stage 1 | validation layer | 证明拓扑成立 |
| session-start context augmentation | Stage 2 | plugin / hook preferred | 优先 `session_start` |
| prompt preprocessing | Stage 2 | plugin / hook preferred | 优先 `user_prompt_submit` |
| artifact detection helpers | Stage 2 | plugin / hook preferred | 先做可选增强 |
| `loadSession` / resume | Stage 2 | ACP core | 接近 continuity 能力 |
| file / artifact bridge | Stage 2 | mixed | 可能 core + plugin/hook 混合 |
| richer session metadata | Stage 2 | mixed | 看 Happy 实际消费面 |
| approval / permission coordination | Stage 3 | mixed | 当前明确不先做大 |
| subagent/background-task visibility | Stage 3 | mixed | 需要更深 runtime 语义 |
| voice-route preservation smoke | Stage 3 | validation layer | 验证 Happy 语音链路与 AWorld 的结合面 |

### Immediate exclusions from the first phase

以下内容不应在第一阶段被“顺手”带入：

- Happy 私有 runner 适配
- voice provider / token / VAD / TTS / STT 本身
- 新建一套 ACP-specific plugin system
- 把 gateway server 变成 ACP 主实现载体
- 为 approval / permission 先造复杂暂停恢复体系

### Pre-implementation checklist

任何一个准备加入近期实现计划的能力，都应先回答四个问题：

1. 它属于上表哪一行？
2. 它的推荐层是 `ACP core`、`plugin/hook`、还是 `validation`？
3. 如果把它从当前阶段移除，ACP host 是否仍然正确可用？
4. 它会不会迫使我们去大改现有 `aworld-cli` 非 ACP 主干？

如果第 3 个答案是“会仍然可用”，或者第 4 个答案是“会扩大非必要改动面”，默认不进入当前阶段。

### What not to do

以下组织方式应明确避免：

- 在 `aworld_gateway/channels/` 下新增 ACP 目录
- 在 `aworld_cli/gateway_cli.py` 里把 ACP host 实现成 gateway 的一个分支
- 用 `happy_*` 命名主实现模块
- 让 `event_mapper` 直接消费 Rich 终端渲染文本
- 为了接入 ACP 而大范围改造现有 CLI 主流程、HTTP/MCP protocol 实现或普通交互式命令路径

## First-phase File Touch-Point Map

为了把“尽量少改现有 aworld-cli”进一步具体化，这里给出第一阶段推荐的文件触点图。

### Create new files

第一阶段优先新增，而不是改老文件：

- `aworld-cli/src/aworld_cli/acp/server.py`
- `aworld-cli/src/aworld_cli/acp/session_store.py`
- `aworld-cli/src/aworld_cli/acp/turn_controller.py`
- `aworld-cli/src/aworld_cli/acp/runtime_adapter.py`
- `aworld-cli/src/aworld_cli/acp/event_mapper.py`
- `aworld-cli/src/aworld_cli/acp/errors.py`
- `aworld-cli/src/aworld_cli/acp/__init__.py`
- 可选：
  - `aworld-cli/src/aworld_cli/acp/debug_client.py`
  - `aworld-cli/src/aworld_cli/commands/acp.py`

### Thin-touch existing files

第一阶段允许薄改的现有文件应尽量限制在少数入口层：

- `aworld-cli/src/aworld_cli/main.py`
  - 仅增加 `acp` 命令分派
  - 不把 ACP 具体逻辑塞进主文件
- `aworld-cli/src/aworld_cli/__init__.py`
  - 如有必要，仅做导出调整
- 可选共享工具提取点：
  - 若 plugin/hook bootstrap 需要抽公共函数，可新增小型 helper 文件，尽量不直接扩张 `runtime/base.py`

### Prefer read-only reuse of existing files

以下现有文件第一阶段更适合作为“被消费对象”，而不是重点修改对象：

- `aworld-cli/src/aworld_cli/executors/local.py`
- `aworld-cli/src/aworld_cli/executors/base_executor.py`
- `aworld-cli/src/aworld_cli/runtime/cli.py`
- `aworld-cli/src/aworld_cli/runtime/base.py`

对这些文件的期望是：

- ACP runtime adapter 读取其现有行为或复用其已有能力
- 若无绝对必要，不直接修改它们的核心执行路径

### Explicitly avoid touching in phase 1

以下区域第一阶段默认不应成为 ACP 实现主战场：

- `aworld-cli/src/aworld_cli/gateway_cli.py`
- `aworld-cli/src/aworld_cli/protocal/http.py`
- `aworld-cli/src/aworld_cli/protocal/mcp.py`
- `aworld_gateway/**`
- 普通交互式 CLI 渲染逻辑，如 `console.py`
- `CLIHumanHandler` 的现有行为本身

如果后续发现必须修改这些区域，应该先在实现计划里把理由单列出来，而不是顺手带入。

## Plugin / Hook Bootstrap Options

既然希望 ACP 尽量利用现有 plugins / hooks，又不希望为了此事把 `aworld-cli` 主干大改，第一阶段需要先选清楚 bootstrap 策略。

### Option A: Reuse full interactive runtime bootstrap

做法：

- 直接复用 `BaseCliRuntime._initialize_plugin_framework()` 一整套交互式 runtime 初始化路径

问题：

- 会把 ACP host 绑定到交互式 CLI runtime 生命周期
- 容易引入与 scheduler、console、HUD 等无关的副作用
- 与“尽量少改、尽量少回归”目标冲突

结论：

- 不推荐作为第一阶段默认方案

### Option B: Extract a tiny shared plugin/hook bootstrap helper

做法：

- 从现有 runtime/plugin 初始化逻辑中抽一个尽量小的共享 helper
- 只负责：
  - 发现 active plugins
  - 加载 plugin hooks / contexts / state store
  - 为 ACP 提供只读扩展表面

优点：

- 能复用现有 plugin/hook 机制
- 不要求 ACP host 依赖完整交互式 runtime
- 更符合“主体实现集中在 `aworld_cli/acp/`”的要求

结论：

- 第一阶段推荐方案

### Option C: First phase without plugin/hook bootstrap

做法：

- 第一阶段先完全不接 plugin/hook
- 等 ACP core 稳定后再补扩展点

优点：

- 风险最低

问题：

- 与“优先复用现有扩展点”目标不完全一致
- 后续补接时可能要重复切接口

结论：

- 可作为 fallback，但不是首选

### Recommended first-phase stance

第一阶段建议采用：

- `Option B` 作为目标方案
- `Option C` 作为保底 fallback

也就是说：

- 若可以以很小代价抽出共享 plugin/hook bootstrap helper，就在第一阶段做
- 若抽取代价明显扩大了非 ACP 改动面，则第一阶段先不强行接入，保持 ACP core 独立正确优先

进一步收敛为实现前约束：

- 如果需要抽共享 helper，优先新增一个单独的 host-owned helper 文件，而不是继续把 ACP 逻辑散落进 `runtime/base.py`
- 该 helper 应只负责插件发现、激活决议、hooks/contexts/state store 组装，不应承担 session/turn/protocol 逻辑
- helper 的推荐落点是泛化目录，例如：
  - `aworld-cli/src/aworld_cli/plugin_capabilities/bootstrap.py`
  - 或 `aworld-cli/src/aworld_cli/acp/plugin_bootstrap.py`，前提是其职责仍然是窄的 bootstrap，而不是把 plugin runtime 重新实现一遍
- 无论 helper 放在哪里，第一阶段都不应要求 ACP host 依赖完整 `BaseCliRuntime.start()` 生命周期

### Recommended shared bootstrap helper contract

如果采用 `Option B`，第一阶段建议把 shared helper 的 contract 直接冻结成“无 session/turn/protocol 语义的纯 bootstrap surface”。

推荐输入：

- `plugin_dirs: list[Path] | list[str]`
- `base_dir` 或等价的 state root 计算基准
- optional feature flags:
  - `load_hooks: bool`
  - `load_contexts: bool`
  - `load_state_store: bool`
  - `sync_commands: bool = false`

推荐输出：

- `plugins`
- `plugin_registry`
- `plugin_hooks`
- `plugin_contexts`
- `plugin_state_store`
- `capabilities` 或等价只读能力摘要
- `warnings` / `skipped_plugins` 之类可诊断结果

明确不应放进 helper 的内容：

- session store
- turn controller
- ACP request dispatch
- runtime adapter
- event mapper
- Rich CLI 渲染状态
- command execution side effects，除非显式打开 `sync_commands`

这样定义的直接收益：

- ACP host 可以复用现有 plugin discovery / activation / hook/context loading 逻辑
- 但不会把 interactive CLI 的命令注册、副作用刷新、console 绑定一并拉进来
- helper 的返回值天然适合在单元测试里直接断言，不需要拉起完整 runtime

### Bootstrap helper side-effect rules

为了把 helper 真正压成“窄 bootstrap”，第一阶段建议再加三条副作用纪律：

- 默认不得调用 `sync_plugin_commands(...)`
- 默认不得刷新 CLI prompt session 或 HUD
- 失败插件应被跳过并记录 warning，而不是让 ACP host 整体初始化失败

这三条与当前目标完全一致：

- ACP phase 1 不依赖 slash command 生态
- ACP phase 1 不依赖 interactive console 生命周期
- plugin/hook 只是增强项，不应反向变成 host 正确性的单点故障

## First-phase Implementation Skeleton

为了让后续实现计划保持收敛，这里把第一阶段拆成固定的实现骨架。每一行都是一个独立实现切片，必须能明确回答：

- 新增代码放在哪里
- 允许薄改哪些旧文件
- 需要哪类测试先兜住
- 是否依赖 plugin/hook bootstrap
- 如果 bootstrap 暂缓，是否仍能先成立

### Skeleton table

| Slice | Primary new files | Allowed thin-touch files | Test types | Plugin/hook dependency | Fallback rule |
| --- | --- | --- | --- | --- | --- |
| CLI command entry | `aworld-cli/src/aworld_cli/acp/__init__.py`; optional `aworld-cli/src/aworld_cli/commands/acp.py` | `aworld-cli/src/aworld_cli/main.py` | command parse smoke | none | 若命令注册方式不稳定，先在 `main.py` 增加最薄分派，不提前整理整套 command system |
| ACP stdio host bootstrap | `aworld-cli/src/aworld_cli/acp/server.py`; `aworld-cli/src/aworld_cli/acp/errors.py` | `aworld-cli/src/aworld_cli/main.py` | stdio integration; stdout/stderr boundary tests | none | host 正确性优先，先不接 plugin/hook |
| Request dispatch and session lifecycle | `aworld-cli/src/aworld_cli/acp/server.py`; `aworld-cli/src/aworld_cli/acp/session_store.py` | none or only import seams | unit + in-process integration | none | 若 `loadSession` 暂不落地，先冻结 `newSession` 主链路 |
| Per-session turn serialization and cancel | `aworld-cli/src/aworld_cli/acp/turn_controller.py` | none | unit race/cancel tests | none | 若 cancel 只能 best-effort，仍必须保证不会挂死或进入未知态 |
| Runtime execution bridge | `aworld-cli/src/aworld_cli/acp/runtime_adapter.py` | optional tiny reuse seam in `aworld-cli/src/aworld_cli/runtime/base.py` or executor-adjacent helper | adapter integration tests | optional | 若抽共享 seam 会扩大改动面，则先在 ACP 目录内包一层 adapter，避免修改 runtime 主干 |
| Runtime event normalization | `aworld-cli/src/aworld_cli/acp/event_mapper.py` | none | mapper unit tests | none | 若 thinking 事件不稳定，允许降级为只保留 tool/text/final-text fallback |
| Optional plugin/hook bootstrap reuse | preferred tiny helper under `aworld-cli/src/aworld_cli/plugin_capabilities/bootstrap.py` or narrow ACP-local bootstrap helper | `aworld-cli/src/aworld_cli/runtime/base.py` only if extracting shared helper is unavoidable | unit + smoke for bootstrap-only path | yes, optional in phase 1 | 若 helper 提取扩大回归面，回退到 phase-1 无 bootstrap 路线 |
| Local self-validation harness | optional `aworld-cli/src/aworld_cli/acp/debug_client.py` | none | local harness smoke | none | 若不做独立 debug client，至少保留 `aworld-cli acp self-test` 或等价自检入口 |
| Happy same-host smoke | no production file required; validation assets only | none | end-to-end smoke | none | 如果尚未具备语音链路验证条件，先冻结文本/tool/cancel 断言 |
| Happy distributed smoke | no production file required; validation assets only | none | topology smoke | none | 若自动化环境不足，可先有手动验证脚本与固定步骤，但不把其写进主实现 |

### Slice-level freeze rules

为了防止实现时跨层串味，第一阶段每个切片还应遵守以下冻结规则：

- `server.py` 可以依赖 `session_store.py` 和 `turn_controller.py`，但不直接吸收 runtime 细节
- `runtime_adapter.py` 输出的应是 host-owned、可测试的规范化事件，不直接输出 ACP frame
- `event_mapper.py` 只消费 adapter 事件，不直接操作 executor 或 session store
- `turn_controller.py` 不负责文本/工具事件映射，只负责并发、终态和 cancel 协调
- plugin/hook bootstrap 即使接入，也只能作为 adapter 前后的小扩展层，不能成为 request dispatch 的前置正确性条件

### Recommended implementation order before a full plan exists

在还没写正式实现计划前，后续讨论默认按下面顺序推进，避免同时扩多个面：

1. 固定 `aworld-cli acp` 命令入口和 stdio host 形态。
2. 固定 session store 与 turn controller 的状态机。
3. 固定 runtime adapter 输出事件模型。
4. 固定 event mapper 的 ACP 输出规则与 fallback。
5. 固定自检入口。
6. 最后再决定 plugin/hook bootstrap 是否在 phase 1 落地。

### Recommended Layer-1 validation entrypoint

对 phase 1，建议把 Layer 1 自证入口固定成：

- required:
  - `aworld-cli acp self-test`
- optional:
  - `aworld-cli/src/aworld_cli/acp/debug_client.py`

推荐理由：

- `self-test` 更适合成为 CI 和本地自动化断言入口
- `self-test` 的 contract 可以保持窄而稳定，不要求交互式输入
- `debug_client.py` 如果存在，更适合做人类开发者的调试壳，而不是 phase-1 correctness 的唯一入口

因此 phase 1 的纪律应是：

- 先冻结 `self-test`
- `debug_client.py` 只能是可选附加层
- 不反过来让 `self-test` 依赖一个交互式 client 工作流

### Recommended `self-test` contract

第一阶段建议把 `aworld-cli acp self-test` 收敛成最小非交互式 contract：

- 启动本地 `aworld-cli acp` 子进程
- 通过 stdio 发送：
  - `initialize`
  - `newSession`
  - `prompt`
  - optional `cancel`
- 断言：
  - 协议可握手
  - session 可创建
  - text/tool/final-text fallback/cancel 语义满足预期
  - `stdout` 只含协议帧
  - `stderr` 只含诊断输出
- 以 machine-checkable 退出码或摘要结果结束

第一阶段不要求：

- REPL 式多轮人工交互
- 完整 IDE client 仿真
- 可视化调试 UI

建议的范围控制：

- 如果 `debug_client.py` 后续存在，它应复用 `self-test` 已经稳定下来的 server-launch / stdio wiring，而不是反过来成为底层实现
- `self-test` 应被视为 validation artifact，而不是产品化用户入口

### Recommended self-test result contract

为了让 Layer 1 验证既能本地跑，也能被 CI 稳定消费，建议第一阶段把 `self-test` 的结果面也冻结成 machine-checkable contract，而不是只打印人类可读日志。

建议约束：

- `stdout`
  - 只输出单个 machine-checkable summary object
  - 不混入进度条、彩色日志、交互式提示
- `stderr`
  - 可输出调试诊断
  - 不作为自动化断言的唯一依据
- exit code
  - `0`: 所有 required automated cases 通过
  - `1`: 至少一个 required automated case 失败
  - `2`: self-test 自身无法完成前置动作，例如 backend 无法拉起或协议握手前即异常退出

建议的最小 summary shape：

```json
{
  "ok": true,
  "summary": {
    "passed": 10,
    "failed": 0,
    "skipped": 0
  },
  "cases": [
    {
      "id": "initialize_handshake",
      "ok": true
    }
  ]
}
```

字段纪律：

- `ok`
  - 必填
  - 仅在所有 required cases 通过时为 `true`
- `summary.passed` / `summary.failed`
  - 必填
  - 用于 CI 和本地脚本直接判断
- `summary.skipped`
  - 可选但推荐保留
  - 如果当前 phase 没有 skip 语义，也可固定为 `0`
- `cases[*].id`
  - 必填
  - 使用稳定 case id，而不是人类文案
- `cases[*].ok`
  - 必填
  - 仅表示该 case 是否通过
- `cases[*].detail`
  - 可选
  - 用于补充失败原因，但测试不应依赖整段 detail 文本做精确匹配

### Recommended self-test assertion matrix

为了避免 `self-test` 变成一个只证明“命令能跑起来”的弱 smoke，第一阶段建议把必须自动化覆盖的断言固定成下面这组最小矩阵。

#### Required automated cases

这些 case 应进入 phase-1 自动化范围：

- `initialize_handshake`
- `new_session_usable`
- `prompt_visible_text`
- `prompt_busy_rejected`
- `cancel_idle_noop`
- `cancel_active_terminal`
- `final_text_fallback`
- `tool_lifecycle_closes`
- `stdout_protocol_only`
- `stderr_diagnostics_only`

这些 case id 的意义是：

- `self-test` summary 可以稳定输出
- Layer 1 自动化可以直接按 id 判断，不依赖自然语言日志
- 未来即使补充 optional smoke，也不应重命名这些 required case id

#### Optional phase-1 manual smoke

这些可以先保留为手工 smoke，而不是 phase-1 自动化硬门槛：

- developer-facing `debug_client.py` interactive flow
- richer plugin/hook bootstrap observation
- Happy same-host operator walkthrough

这条切分的目的很直接：

- 先保证 ACP host correctness 的核心断言可以在本地和 CI 稳定复现
- 不让交互式调试壳或部署环境问题阻塞 Layer 1 的自动化可信度

## ACP Host Shape

结合 OpenClaw 的 ACP 文档与实现，AWorld 首版应借鉴的是“薄宿主桥接”形态，而不是它的 Gateway 产品边界。

对 AWorld 来说，建议固定为：

- Happy CLI/daemon 在 worker host 上以子进程方式拉起 `aworld-cli acp`
- `aworld-cli acp` 通过 `stdin/stdout` 与 Happy ACP runner 通信
- `stderr` 只输出诊断日志，不承载协议消息
- 进程内部自己维护 ACP session store、turn controller 与 runtime adapter

Why:

- 这与 Happy 现有的 backend runner 形态天然一致，不需要 Happy 改代码。
- 它把实现边界锁在 AWorld 宿主层，不要求把现有 gateway server 变成 Happy 的主接入点。
- 同机与分布式部署下，AWorld backend 的运行位置都保持一致：始终在真正执行 agent 的 worker host 上。

与 Happy 的关系需要进一步明确：

- AWorld 对接的是 Happy CLI/daemon 暴露出来的 generic ACP host contract
- AWorld 不需要知道 Happy 内部是如何组织 codex/gemini/claude 等私有 agent runner 的
- 对 AWorld 来说，Happy CLI/daemon 只是“负责拉起 ACP backend 并消费 ACP session updates 的宿主”

这也意味着：

- AWorld 主实现目录不应出现 Happy 定制语义
- 如果后续要增加 Happy 专项验证工具，也应优先落在验证层，而不是主实现层

明确排除：

- 第一阶段不要求 Happy 通过 HTTP 调用 AWorld gateway server
- 第一阶段不要求 AWorld 先变成一个长期运行的独立远程服务再让 Happy 接入
- 现有 `aworld-cli gateway server` 只作为可选宿主基础设施复用来源，不是 ACP 方案的主协议边界

## Happy Capability Preservation

这是当前方案必须额外锁死的一条原则。

本方案的目标不是把 Happy 降级成“只是一个文本聊天壳子”，而是让 Happy 整套 app/web/server/daemon/control-plane 继续成立，AWorld 只是作为其中一种 backend 接入。

因此需要明确：

- Happy 现有端侧能力默认都应被视为需要保留
- 语音能力是明确需要保留的能力之一
- 这些能力的实现责任优先仍在 Happy 自身，而不是要求 AWorld 复制 Happy 端的 voice stack
- AWorld backend 的设计不能阻断这些能力后续继续工作

对语音的具体含义：

- Happy app 侧的 mic、voice session、VAD、TTS/STT、voice routing 继续属于 Happy
- AWorld 不需要为了接入去实现 Happy 的 voice provider、voice token、或 app 侧状态机
- 但 AWorld backend 最终必须能够承接由 Happy 语音链路转译出来的会话控制与消息流，而不是只支持手动文本输入场景

因此，第一阶段虽然先从文本 ACP turn 打通，但文档里必须把“保留 Happy 语音能力”写成终态约束，而不是可选增强项。

### Capability Classes

为了避免后续讨论时把“Happy 全量能力保留”误解成“所有能力都要求 AWorld 立刻原样实现”，这里把能力分成三类。

#### Class A: Happy-native, backend-transparent capabilities

这类能力主要由 Happy app/web/server/voice pipeline 自己负责，AWorld backend 不需要复制实现，但不能破坏它们继续工作。

包括：

- 语音会话生命周期
- mic / VAD / TTS / STT
- 当前 session 路由与 focus 切换
- 端侧 prompt batching 与 contextual updates

对 AWorld 的要求：

- 能承接这些能力最终落下来的 session 控制与文本 turn
- 不要求 Happy 为了接入 AWorld 改写它自己的 voice/session routing 逻辑

语音为什么属于这一类：

- 从 Happy `voice-architecture.md` 来看，语音链路的核心是 Happy 端把语音交互转译成当前 session 上的消息与工具调用
- 这意味着 backend 看到的本质上仍然是 session-level prompt / response / tool interaction
- 因此 AWorld 不需要自己实现语音栈，但必须兼容这种经由 Happy 路由后的 session 行为

#### Class B: Backend-visible control capabilities

这类能力直接取决于 backend contract，是 AWorld ACP backend 第一阶段就必须先打通的部分。

包括：

- session 创建
- prompt turn 执行
- 文本流式输出
- thinking 输出（可选）
- tool start/end
- cancel / abort

这些能力决定了 Happy 是否能把 AWorld 当成一个可控制 backend 使用。

#### Class C: Backend-dependent advanced capabilities

这类能力最终也要支持，但它们比 Class B 更依赖 AWorld backend 额外提供稳定语义。

包括：

- session 恢复 / 列表
- artifact/file 富展示
- approval / permission 协调
- subagent / background task 可视化
- 更丰富的 session metadata / mode / model / usage surface

对这类能力的结论是：

- 它们属于最终集成目标的一部分
- 但不应阻塞第一阶段的 host contract 打通
- 后续阶段要逐步从 Class B 扩展到 Class C，而不是停在文本链路

## Session Model

OpenClaw 里最值得借鉴的点不是“Gateway session key”本身，而是“ACP session 与后端运行会话的一对一映射”。

AWorld 首版建议采用更简单的 host-local 映射：

- 一个 ACP `sessionId` 映射到一个 AWorld host-local session record
- 一个 host-local session record 对应一个稳定的 `aworld session_id`
- 该 record 至少持有：
  - `acp_session_id`
  - `aworld_session_id`
  - 当前运行中的 turn task / cancel handle
  - 选定 agent 标识
  - 当前 workspace / cwd 上下文

第一阶段行为建议：

- `newSession` 创建新的隔离会话，并为其分配新的 `aworld_session_id`
- 一个 ACP session 在 bridge 进程生命周期内始终绑定同一个 `aworld_session_id`
- 若 bridge 进程退出，ACP session store 可以丢失；真正的 workspace 与 transcript 连续性依赖 AWorld 自己已有的 session/workspace 机制，而不是 ACP bridge 额外发明新的持久化层

这与 OpenClaw 的思路一致：ACP bridge 只负责桥接期内的 session 绑定，不把自己做成新的系统级 session source of truth。

第一阶段范围收敛说明：

- 第一阶段不要求实现 `loadSession`
- 第一阶段不要求通过 ACP 暴露“历史 session 浏览/恢复”能力
- 这只是阶段收敛，不代表最终不需要；如果 Happy 端侧实际依赖 session 恢复，需要在后续阶段补齐

## Turn Execution Model

OpenClaw 的 bridge 明确把“ACP prompt -> 后端一次 turn”作为最小运行单元。AWorld 也应采用这个边界。

第一阶段建议：

- 每个 ACP session 同时只允许一个活跃 turn
- `prompt` 到来时，host-local session controller 为该 turn 分配 request id，并启动一次 AWorld executor 调用
- 一个 `prompt` RPC 的生命周期等于一个 AWorld turn 的生命周期；只有 turn 终态到来后，`prompt` 才返回
- 在活跃 turn 结束前，如果同一 session 再收到新的 `prompt`，第一阶段立即返回 busy / conflict 类错误，而不是排队或隐式抢占前一轮
- `cancel` / `abort` 只作用于当前 session 的当前活跃 turn

Why:

- 不修改 `aworld/core` 的前提下，显式串行 turn 比隐式抢占更可控
- Happy 端侧要的是稳定的远控主链路，不是第一阶段就支持复杂并发会话调度

关于取消：

- 第一阶段只承诺 best-effort cancel
- 实现上由 `aworld-cli` 外层持有 asyncio task / cancellation handle 并向下传播
- 如果 session 当前没有活跃 turn，`cancel` 直接返回成功 no-op，不报错
- 如果 cancel 发生时底层运行时已经接近完成，可能出现 cancel 请求成功送达但最终仍收到正常完成事件；这一点需要在 Happy 接入文档中明确为可接受的 race

对 Happy 的精确定义：

- Happy 侧不需要 AWorld 额外发明独立的“ACP status update”体系
- Happy 当前可以基于 `prompt` 调用状态、文本 chunk、tool start/end 自己推导 `running/idle`
- 因此第一阶段 AWorld 只要把 turn 生命周期与文本/tool 更新做准确，不需要把范围扩到额外的 status protocol 设计

### Busy / Cancel Semantics

结合 OpenClaw 的 turn 控制方式和 Happy 当前 `AcpBackend` 的使用方式，第一阶段推荐固定为：

- `busy` 语义：
  - 同一 ACP session 上若已有未结束的 `prompt`
  - 新 `prompt` 立即失败
  - 不做隐式排队
  - 不做自动 cancel-and-replace
- `cancel` 语义：
  - 仅针对当前 session 当前活跃 turn
  - 没有活跃 turn 时为 no-op success
  - backend 收到 cancel 后应尽快停止继续发送新的文本/tool 事件
  - 若底层 race 导致终态仍为 completed，Happy 侧接受该结果，但不得再启动新的工具轮次

这样做的原因很直接：

- Happy 当前主链路是“一个会话，一次输入，一次 turn”
- 对 AWorld 来说，不改 `aworld/core` 时，显式拒绝并发 turn 比引入队列语义更安全
- 对后续扩展也更可控，因为 queue / resume / multi-turn overlap 都是独立复杂度

### Turn state machine

为了避免后续在实现时把 `busy` / `cancel` 做成隐式约定，第一阶段应显式采用下面这组 session-local turn 状态：

| State | Meaning | Allowed incoming method | Required behavior |
| --- | --- | --- | --- |
| `idle` | 当前 session 没有活跃 turn | `prompt`, `cancel` | `prompt` 可启动新 turn；`cancel` 为 no-op success |
| `running` | 当前 session 正在执行 turn | `cancel` | 新 `prompt` 立即 busy 失败；`cancel` 进入 `cancelling` 或直接终止 |
| `cancelling` | host 已接收 cancel 并向下传播 | `cancel` | 额外 `cancel` 仍返回 success/no-op；不得再启动新 tool call |
| terminal | `completed` / `failed` / `cancelled` 已到达 | `prompt`, `cancel` | turn 结束后 session 回到 `idle`；`cancel` 为 no-op success |

第一阶段的强约束：

- `running` 与 `cancelling` 都视为 busy
- busy 判断是 per-session，而不是全局
- 一个 session 的 turn 进入 terminal 后，才能接受下一次 `prompt`
- `cancel` 不要求传 `turnId`，因为第一阶段只允许单 active turn

状态迁移建议固定如下：

```text
idle --prompt--> running
running --cancel accepted--> cancelling
running --runtime completed--> terminal(completed)
running --runtime failed--> terminal(failed)
cancelling --runtime acknowledges stop--> terminal(cancelled)
cancelling --runtime races to normal completion--> terminal(completed)
cancelling --runtime errors during stop--> terminal(failed) or terminal(cancelled)
terminal --> idle
```

这里要特别写死的一点是：

- `cancelling -> completed` 是允许的 race 结果
- 但一旦进入 `cancelling`，backend 不得再开启新的工具轮次，也不应继续主动扩展输出流
- 如果 runtime 已经产出尾部完成结果，允许 Happy 最终看到 `completed`

### Prompt / cancel truth table

为了让 Happy 接入和 AWorld 自测都能基于同一套断言，第一阶段建议把关键请求行为固定成下面的真值表：

| Incoming method | Session state | Result | Notes |
| --- | --- | --- | --- |
| `prompt` | `idle` | accepted | 创建新 turn，并进入 `running` |
| `prompt` | `running` | rejected as busy/conflict | 不排队，不抢占，不隐式 cancel |
| `prompt` | `cancelling` | rejected as busy/conflict | 直到前一 turn 终态到达 |
| `cancel` | `idle` | success no-op | 不视为错误 |
| `cancel` | `running` | success best-effort | 尝试进入 `cancelling` |
| `cancel` | `cancelling` | success no-op | 不重复触发新的停止链路 |
| `cancel` | unknown session | protocol-level not-found or equivalent explicit error | 这是 session correctness，不应伪装成 no-op |

这张表对应的范围控制非常重要：

- 第一阶段不做 cancel-and-replace
- 第一阶段不做 prompt queue
- 第一阶段不做 cross-session cancel
- 第一阶段不做历史 turn 的定向 cancel

## Happy-Aligned ACP Subset

参考 OpenClaw 的 bridge 设计，同时对照 Happy 当前 `AcpBackend` 的实际调用路径，AWorld 第一阶段应只承诺以下最小方法集合：

- required:
  - `initialize`
  - `newSession`
  - `prompt`
  - `cancel`
- optional but not required in the first phase:
  - `loadSession`
  - `unstable_listSessions`
  - `setSessionMode`
  - session config / model related extensions

Why:

- Happy 当前启动后首先做 `initialize` 和 `newSession`
- 日常发送消息依赖的是 `prompt`
- 用户主动停止依赖的是 `cancel`
- 其余方法虽然在 Happy 代码里有兼容入口，但不是当前目标链路的必要条件

这也是本方案相对 OpenClaw 的一个关键收敛点：

- 借鉴 OpenClaw 的 method 组织方式
- 但不因为 OpenClaw 支持更多 session/runtime control，就把 AWorld 第一阶段一并做掉

### Method contract details for phase 1

为了避免后续实现时把“支持某个 method”理解成模糊兼容，第一阶段应把这些 method 的合同写得更具体。

#### `initialize`

第一阶段要求：

- backend 正常返回协议初始化响应
- 明确声明自己支持的最小 session/update 能力集合
- 不要求在 `initialize` 阶段暴露完整 mode/model/config 能力树
- capability advertisement 必须与真实实现一致，不能为了未来阶段预先 over-claim

第一阶段不要求：

- 提供完整的 session metadata catalog
- 提供 Happy 私有能力声明

#### `newSession`

第一阶段要求：

- 为新 ACP session 创建新的 host-local session record
- 为其绑定稳定的 `aworld_session_id`
- 返回后该 session 立即可用于 `prompt`

第一阶段不要求：

- 通过 `newSession` 暴露复杂的 mode/model 切换
- 在 `newSession` 上承载 Happy 私有 metadata 语义

#### `prompt`

第一阶段要求：

- 一次 `prompt` 对应一次 turn
- 仅在 session `idle` 时接受
- turn 完结前维持该调用生命周期
- 通过 session updates 持续输出文本 / thinking / tool 事件

第一阶段允许的输入收敛：

- 优先支持 Happy 当前主链路必需的文本 prompt 内容
- 若 ACP prompt 中带有附加 block/resource，第一阶段应采用“显式收敛”的处理方式：
  - 能稳定转成文本的部分可转成文本
  - 无法稳定映射的部分不应 silently invent 新语义

### Prompt content normalization for phase 1

虽然 Happy 当前主链路实际发送的是单个 `text` block，但 AWorld ACP 是泛化能力，因此 phase 1 仍应把输入收敛规则写清楚，而不是默认“只会收到文本”。

第一阶段建议采用如下规范化规则：

| ACP prompt block type | Phase-1 behavior | Notes |
| --- | --- | --- |
| `text` | required support | 直接按用户正文拼接 |
| `resource` with embedded text | optional normalize-to-text | 只有在能稳定提取文本时才合并入 prompt |
| `resource_link` | optional normalize-to-text reference | 可降级成稳定文本引用；不要求 phase 1 变成真实下载/读取能力 |
| `image` | not supported in phase 1 unless a stable host-layer bridge exists | 未实现则不得在 capability advertisement 中声称支持 |
| `audio` or other rich blocks | not supported in phase 1 | 显式 defer，不做隐式吞并 |

规范化原则：

- prompt 规范化发生在 host 层，而不是 `aworld/core`
- 规范化输出应是 AWorld runtime 可以稳定消费的单一文本 prompt，外加明确可桥接的附件信息
- 不能稳定映射的 block，要么显式忽略并记录为 unsupported，要么直接返回明确错误；不能偷偷转换成不可解释的正文
- 不允许把终端诊断、ACP metadata、宿主注入状态混进用户正文

对当前范围的直接约束：

- 如果 AWorld phase 1 没有稳定的 image bridge，就不应在 `initialize` capability 里声称 `promptCapabilities.image = true`
- 如果 `resource_link` 只是被降级成文本引用，这应被视为“文本兼容收敛”，而不是完整 artifact/file 支持
- Happy 的语音保留目标不改变这里的结论，因为 Happy 语音在 backend 侧最终仍应落到 session-level prompt/control，而不是要求 phase 1 立刻支持 audio ACP blocks

#### `cancel`

第一阶段要求：

- 仅作用于当前 session 的 active turn
- active turn 不存在时返回 success/no-op
- active turn 存在时发起 best-effort stop

第一阶段不要求：

- 支持指定历史 turn 或指定 tool call 的 cancel
- 提供 resume/continue 语义

### Recommended minimal `initialize` / `newSession` payload for phase 1

为了防止 phase 1 因为“ACP 看起来还能声明更多东西”而过度承诺，建议把 `initialize` 和 `newSession` 的最小诚实声明直接固定下来。

#### Recommended phase-1 `initialize`

第一阶段推荐声明：

- `protocolVersion`
- `agentInfo`
- `authMethods`
- `agentCapabilities`
  - `loadSession: false`
  - `promptCapabilities`
    - `image: false`
    - `audio: false`
    - `embeddedContext: false`，除非 text-bearing resource / resource_link 的 host-layer normalization 已经被明确实现并验证
  - `mcpCapabilities`
    - `http: false`
    - `sse: false`

第一阶段不建议声明：

- `sessionCapabilities.list`
- `modes`
- `models`
- 任何需要 Stage 2 continuity 或更高阶段支持才能成立的 capability

为什么要保守到这个程度：

- Happy 会读取这些能力，但 phase 1 的目标不是把所有可选控制面都打开
- 一旦 over-claim，后面就会被迫实现更多非主链路能力，或者制造“声明支持但实际不可用”的协议债
- 对当前方案来说，`initialize` 的价值是诚实表达 host contract，而不是提前暴露路线图

#### Recommended phase-1 `newSession`

第一阶段推荐返回：

- `sessionId`

第一阶段可以不返回：

- `configOptions`
- `modes`
- `models`
- richer session metadata

这条约束的含义是：

- `newSession` 在 phase 1 只负责建立一个可立即执行 `prompt` 的 session
- 不负责把 mode/model/permission/config 生态一次性补齐
- 若未来某些配置能力确实要接入，应进入后续阶段并在 capability advertisement 中同步升级

#### Optional-but-not-required phase-1 session metadata

如果实现时发现 Happy 的某些 UX 明显受益于少量 session metadata，可以补，但必须满足两条前提：

- 该 metadata 不会扩大 phase-1 scope 到 continuity、mode/model、permission orchestration
- 即使去掉该 metadata，主链路仍然成立

默认结论仍然是：

- phase 1 不要求 `available_commands_update`
- phase 1 不要求 config/mode/model payload
- phase 1 先把主链路做对，再考虑 richer metadata

### Why `available_commands_update` stays out of phase 1

`available_commands_update` 容易把 ACP host 设计重新拉回“CLI 命令发现/展示协议”，这不符合当前范围。

第一阶段建议明确：

- backend 不要求发送 `available_commands_update`
- Happy 集成成功与否不得依赖 `available_commands_update`
- 即使 plugin/hook bootstrap 发现了可用 command/context/helper，也不要求 phase 1 把它们投影成 ACP command catalog

这样收敛的原因：

- Happy 当前主链路依赖的是 session、prompt、text、tool、cancel
- AWorld 现有 plugins/hooks 更适合先服务 runtime augmentation，而不是先抽象成 command metadata surface
- 如果后续确实需要 command catalog，应作为 post-phase-1 的可选 metadata 能力单独设计，而不是隐式混进主链路

因此在验证口径上也应固定：

- Layer 1 不因缺少 `available_commands_update` 判定失败
- Layer 2 不把 `available_commands_update` 作为 Happy 全链路成立的前置条件

### Why no extra phase-1 status method/update is required

结合 Happy 当前 `AcpBackend` / `AcpSessionManager` 的消费方式，第一阶段不需要为 AWorld 额外设计一套独立的 status 协议面。

原因是：

- Happy 已经基于 `prompt` 生命周期、message chunks 和 tool lifecycle 推导 turn 活跃状态
- Happy 对文本、thinking、tool 的消费比对额外 status update 更关键
- 额外 status 面如果先做，很容易把范围扩到 “running/idle/error 还有哪些子状态” 这类并非当前主链路必要的问题

因此第一阶段应固定：

- 优先保证 `agent_message_chunk`、optional `agent_thought_chunk`、`tool_call`、`tool_call_update` 的准确性
- 不把范围扩张到新的 host-specific status update 设计

## End-State Capability Roadmap

为了同时满足“最终要接入 Happy 全体系”和“当前不要把项目范围做爆”，建议把能力演进明确拆成阶段，而不是把第一阶段写成最终状态。

### Stage 1: Host contract and interactive control

目标：

- 让 Happy 能把 AWorld 当作一个可控制 backend 跑起来
- 跑通 session / turn / text / tool / cancel 的主链路
- 建立 AWorld ACP host 的独立验证能力

这是当前 change 最主要聚焦的阶段。

### Stage 2: Happy-facing continuity capabilities

目标：

- 补齐 session continuity 相关能力
- 补齐 artifact/file 等更接近 Happy 现有会话体验的能力
- 让 Happy 接入 AWorld 后，不只是“能发消息”，而是逐步接近 Happy 既有 session 使用体验

建议纳入该阶段的内容：

- `loadSession` / session resume
- file / artifact bridge
- richer session metadata
- 更稳定的 final-text / tool-state 恢复语义

### Stage-2 continuity gate for `loadSession`

`loadSession` 不应被视为一个“顺手补上的 method”。对当前方案来说，它是 Stage 2 的 continuity gate，只有在以下前提都成立时才应该正式进入实现。

必须先满足的前提：

- 已有办法把外部 ACP `sessionId` 稳定重绑定到正确的 `aworld_session_id`
- agent identity、workspace/cwd、以及必要的 host-local session metadata 可以被一致恢复
- 对“bridge 进程重启后是否还能恢复”的语义已有明确边界，而不是依赖偶然的内存态
- 若断开前存在 active turn，重新 `loadSession` 时不会把未知中的旧 turn 误判为可继续控制的新 turn
- Happy 侧对 resume 的预期已经被验证为“session continuity”，而不是“active turn reattachment”

建议先明确排除的误解：

- `loadSession` 不等于恢复一个仍在后台运行但状态未知的老 turn
- `loadSession` 不等于自动补齐所有历史 tool lifecycle
- `loadSession` 不应先于 file/artifact/metadata continuity 的最小模型而单独落地

更具体地说，Stage 2 的 `loadSession` 应优先解决的是：

- 会话身份连续
- workspace/cwd 连续
- 后续新 prompt 落到同一个 AWorld session 上

而不是先承诺：

- 中途重连后还能无损接回所有 in-flight tool 状态
- 把之前所有 runtime 内部细节重新投影回 Happy UI

这条 gate 的意义在于：

- 防止 `loadSession` 以一个过度简化的 form 提前进入 phase 1
- 防止为了满足 `loadSession` 去反向侵入 `aworld/core`
- 让 continuity 能力与当前主链路能力分阶段验证，而不是混成一个大目标

### Stage 3: Full capability alignment

目标：

- 让 AWorld backend 与 Happy 既有能力面完成更完整的对齐
- 特别是那些虽然由 Happy 主导，但最终仍要求 backend 提供更丰富配合语义的能力

建议纳入该阶段的内容：

- approval / permission coordination
- subagent / background task visualization
- Happy 原生语音场景下与 AWorld backend 的长期稳定协作

这里再强调一次：

- Stage 3 不是可选项
- 它是最终目标的一部分
- 只是当前 change 不把所有阶段一次性展开成实现任务

## Runtime-to-ACP Mapping

OpenClaw 的另一条可直接借鉴原则是：不要把终端渲染逻辑直接暴露给 ACP，而是单独做 runtime event mapper。

AWorld 第一阶段的 mapper 应直接消费现有 `LocalAgentExecutor` / `Runners.streamed_run_task(...)` 暴露出来的运行输出，而不是复用 CLI Rich 渲染结果。

### Recommended runtime adapter event schema

虽然第一阶段会消费 AWorld 现有 `ChunkOutput`、`MessageOutput`、`ToolResultOutput`，但 `runtime_adapter` 不应把这些对象原样暴露给 `event_mapper`。建议先冻结一层 host-owned normalized event schema。

所有 normalized events 都应具备以下公共字段：

| Field | Required | Meaning |
| --- | --- | --- |
| `event_type` | yes | 事件类型，值来自固定枚举 |
| `seq` | yes | 当前 turn 内单调递增序号，从 `1` 开始即可 |
| `timestamp_ms` | no | host 观察到该事件的本地时间，便于调试 |

推荐最小事件集合：

| Event type | Required fields | Meaning |
| --- | --- | --- |
| `text_delta` | `text` | 用户可见正文增量 |
| `thought_delta` | `text` | 可选 reasoning/thinking 增量 |
| `tool_start` | `tool_call_id`, `tool_name`, optional `title`, optional `kind`, optional `raw_input` | 工具开始 |
| `tool_end` | `tool_call_id`, `status`, optional `raw_output`, optional `error` | 工具结束 |
| `final_text` | `text` | 无稳定 chunk 时的最终正文兜底 |
| `turn_error` | `code`, `message`, optional `retryable`, optional `origin` | 当前 turn 终态错误 |

补充字段纪律：

- `text_delta.text`
  - 必须是用户可见正文
  - 不混入 tool status、HUD 文本、诊断日志
- `thought_delta.text`
  - 只有在可以稳定识别为 thought 时才允许出现
- `tool_end.status`
  - phase 1 只要求 `completed` 或 `failed`
- `turn_error.origin`
  - 可选
  - 建议值仅限 `runtime` 或 `host`
  - 用于区分运行时失败和宿主层 admission/bridging failure

### Recommended normalized event examples

下面的例子不是要求 wire format 一字不差，而是冻结字段级 contract。

```json
{"event_type":"text_delta","seq":1,"text":"正在检查仓库结构。"}
{"event_type":"tool_start","seq":2,"tool_call_id":"tool_1","tool_name":"shell","title":"Run rg"}
{"event_type":"tool_end","seq":3,"tool_call_id":"tool_1","status":"completed"}
{"event_type":"final_text","seq":4,"text":"结论是建议走 ACP backend。"}
{"event_type":"turn_error","seq":5,"code":"AWORLD_ACP_REQUIRES_HUMAN","message":"Human approval/input flow is not bridged in phase 1.","retryable":true,"origin":"runtime"}
```

这些例子的约束重点是：

- `event_mapper` 只依赖这些固定字段，不依赖原始 `Output` 类型
- `seq` 为 turn 内排序真值来源
- `turn_error` 作为 terminal event 独立存在，而不是文本事件的特殊变体

### Recommended `tool_call_id` normalization policy

`tool_call_id` 是 phase 1 最容易失配的点之一，因此建议在 adapter 层就固定优先级和闭合规则，而不是留给 `event_mapper` 临场推断。

推荐优先级：

1. 若 `ToolResultOutput.metadata.tool_call_id` 存在，优先使用它
2. 否则若 `ToolResultOutput.origin_tool_call.id` 存在，使用它
3. 否则若 `MessageOutput.tool_calls[*].data.id` 或等价 `ToolCall.id` 存在，使用它
4. 只有当以上都不存在时，adapter 才为当前 turn 生成 host-local synthetic id

synthetic id 规则建议：

- 仅在当前 turn 内有效
- 使用泛化前缀，例如 `acp_tool_<turn-local-seq>`
- 不把 Happy 或具体宿主写进 id
- 必须保证在同一 turn 内稳定复用，而不是每次看到相关输出都重新生成

### Tool lifecycle closure rules

在 phase 1，`runtime_adapter` 应保证以下闭合纪律：

- 一个 `tool_call_id` 在单 turn 内最多出现一次 `tool_start`
- `tool_end` 必须引用一个已知的 `tool_call_id`
- 若先看到 `ToolResultOutput`，但之前没有可识别的 start，adapter 可以：
  - 先合成一个最小 `tool_start`
  - 紧接着给出对应 `tool_end`
- 不允许把无法闭合的 tool result 直接丢给 `event_mapper`

这条规则的意义：

- Happy 需要稳定闭合工具状态
- AWorld 现有输出可能不是严格按 ACP 想要的开始/结束顺序出现
- 这类“补 start / 保闭合”的脏活应留在 adapter，而不是扩散到 mapper 或 Happy

### Tool-call identity scope discipline

为了防止后续把 `tool_call_id` 语义做大，第一阶段还应明确：

- `tool_call_id` 只要求在单 turn 内稳定
- 不要求跨 session、跨进程重启、跨 `loadSession` 保持同一 identity
- Stage 2 如果要做更强 continuity，再单独定义更强 identity 语义

这里刻意不放入 schema 的内容：

- ACP frame 本身
- CLI/Rich 渲染片段
- HUD/status line 文本
- 完整原始 `Output` 对象
- session store / turn controller 的可变状态

这样做的意义是：

- `event_mapper` 只面对稳定、窄的 host-owned 事件
- adapter 负责吸收 AWorld 运行输出的不规则性
- 后续如果 AWorld 内部 output 结构调整，优先改 adapter，不把波动扩散到 ACP output 层

### Adapter normalization rules

为了防止 `runtime_adapter` 退化成“简单透传”，第一阶段建议再写死以下规范化规则：

- `ChunkOutput`
  - 若能提取用户可见正文，转成 `text_delta`
  - 若能稳定提取 reasoning，再转成 `thought_delta`
- `MessageOutput`
  - 用于补齐 final assistant 文本
  - 若前面没有稳定正文 chunk，则转成 `final_text`
  - 若其中包含可识别 tool call 信息，可补发 `tool_start`
- `ToolResultOutput`
  - 只转成 `tool_end`
  - 不把整个 result object 直接外泄给 ACP
- 其它内部输出类型
  - 若与 phase-1 主链路无关，adapter 可以忽略
  - 不应因为看到未知输出就临时扩张 event schema

### Adapter / mapper boundary

建议把 `runtime_adapter.py` 与 `event_mapper.py` 的边界进一步冻结为：

- `runtime_adapter.py`
  - 输入：AWorld runtime outputs
  - 输出：normalized runtime events
- `event_mapper.py`
  - 输入：normalized runtime events
  - 输出：ACP session updates

不允许的反向耦合：

- `event_mapper.py` 直接 `isinstance(..., ChunkOutput)`
- `runtime_adapter.py` 直接拼 ACP `sessionUpdate`
- `runtime_adapter.py` 直接操作 session busy/cancel 状态

### Why raw output passthrough is forbidden

如果不先冻结这层 schema，后续实现很容易滑向：

- 为了省事直接让 `event_mapper` 处理多个 AWorld 原始输出类
- 单元测试绑定到 AWorld 内部 output 细节
- 一处 output 字段变化引发 ACP host 多处连锁修改

因此 phase 1 应明确：

- raw output passthrough 不是可接受实现
- normalized event schema 是 `runtime_adapter` 的存在前提，而不是可选重构

结合 Happy 当前 `sessionUpdateHandlers` 的实际消费逻辑，建议的第一阶段映射如下：

| AWorld runtime signal | ACP-facing behavior |
| --- | --- |
| `ChunkOutput` 文本增量 | 发送 `agent_message_chunk` |
| 可区分的 thinking / reasoning 文本 | 可选发送 `agent_thought_chunk`；如果现有输出没有稳定边界，第一阶段可以不发 |
| `ChunkOutput` / `MessageOutput` 中的 tool call 信息 | 发送 `tool_call`，带稳定 `toolCallId`；若缺省状态则按 `in_progress` 解释 |
| `ToolResultOutput` 或等价结束信号 | 发送 `tool_call_update`，状态为 `completed` / `failed` |
| 最终 assistant 输出完成 | 结束 `prompt` RPC；必要时先补发最后文本 chunk |
| 执行异常 | 让 `prompt` 以错误结束 |

补充约束：

- Happy 侧实际依赖的是 `agent_message_chunk` / `tool_call` / `tool_call_update` / optional `agent_thought_chunk`
- 第一阶段不需要设计额外 ACP session status update 类型
- 如果某轮没有稳定的 chunk 增量，但有最终 `MessageOutput`，backend 仍要在 `prompt` 返回前补发一次完整 `agent_message_chunk`，避免 Happy 端只看到 turn 结束而没有正文
- `tool_call` 和 `tool_call_update` 必须复用同一个稳定 `toolCallId`，否则 Happy 侧无法可靠闭合工具状态
- 不把 Rich 样式、终端状态行、token HUD 文本直接映射到 ACP
- `stderr` 诊断日志不得混入 ACP `stdout`
- `initialize` 中声明的 prompt capabilities 必须和这里实际可桥接的输入类型一致

### Event Detail Rules

为避免把 ACP 事件设计扩成通用运行时协议，第一阶段只定义 Happy 当前真正需要的细节：

- `agent_message_chunk`
  - 只承载用户可见正文文本
  - 以 `content.text` 作为稳定输出面
  - 增量发送
  - 不混入思考文本、日志、状态描述
- `agent_thought_chunk`
  - 可选
  - 只有在 AWorld 能稳定区分 thought 与 output 时才发送
  - 如果不能稳定区分，第一阶段直接省略，不做猜测性拆分
- `tool_call`
  - 表示工具开始
  - 必须包含稳定 `toolCallId`
  - 同一个 `toolCallId` 在单 turn 内只能 start 一次
  - `title` / `kind` 能提供则提供，不能稳定推导时可降级
- `tool_call_update`
  - 只用于闭合已有 `toolCallId`
  - 第一阶段只要求 `completed` / `failed`
  - `rawOutput` 为可选调试信息，不要求 Happy 第一阶段强消费
- `final-text fallback`
  - 若没有稳定 chunk，但有最终 assistant 文本，必须在 `prompt` 正常返回前补发至少一个 `agent_message_chunk`
  - fallback 文本应作为正常 assistant output，而不是错误文本或调试文本
- terminal ordering
  - `tool_call_update` 必须先于 turn 终态闭合
  - final-text fallback 必须先于 `prompt` 成功返回
  - `cancel` 被接受后不得再发新的 `tool_call` start

这样既借鉴了 OpenClaw 的 translator 结构，也避免把 AWorld ACP backend 设计成大而全的事件总线。

### Event ordering contract

为了降低 Happy 集成方和 AWorld 自测的歧义，第一阶段应采用如下事件时序约束：

1. `prompt` 被接受后，session 进入 `running`
2. backend 可发送零到多个 `agent_message_chunk`
3. backend 可穿插发送零到多个 optional `agent_thought_chunk`
4. 每个 tool 轮次按 `tool_call` -> `tool_call_update` 闭合
5. 若没有稳定 chunk 但有最终 assistant 文本，则先发 final-text fallback
6. 最后才允许 `prompt` 调用成功返回或以 terminal error 结束

补充约束：

- 第一阶段不要求“每个文本 chunk 都对应一个确定 token 边界”，只要求文本可稳定拼接
- Happy 当前会自行聚合文本和 thinking，因此 AWorld 不需要为了 phase 1 过度切 chunk
- 但 AWorld 也不应把整个长回复都拖到最后一次性输出，除非 runtime 确实没有稳定 streaming 能力

### Recommended `turn_error` translation rules

`turn_error` 是 normalized runtime event，不是 phase-1 新增的 ACP update 类型。它的职责是把“这轮 turn 失败了”稳定传给 `event_mapper`，再由后者让 `prompt` 以结构化失败结束。

建议固定如下规则：

- `turn_error` 一旦出现，该 turn 不得再产生新的：
  - `agent_message_chunk`
  - `agent_thought_chunk`
  - `tool_call`
- 如果在 `turn_error` 出现前，某个 `tool_call_id` 已经发出 `tool_call` 但尚未闭合：
  - host 必须在 `prompt` 失败返回前闭合该 lifecycle
  - 闭合方式为补发一个 `tool_call_update(status=failed)`
  - 该失败 detail 可以是 mapper-local/synthetic 的最小说明，不要求扩张 phase-1 public error taxonomy
- `turn_error` 不得被降级成普通 assistant 文本
- `turn_error` 不得被伪装成 `cancelled`，除非这次终止确实来自已接受的 `cancel`

对已有部分文本输出的处理也应固定：

- 如果失败前已经产生了合法的 `agent_message_chunk`，这些文本仍然保留为已发生输出
- 但失败点之后不得继续追加新的 assistant 文本
- Happy 侧应看到“本轮失败”，而不是“backend 停机”或“会话卡死”

验证分层建议：

- Layer 1
  - 断言 `prompt` 失败 detail 中包含稳定 `code`
  - 断言 `turn_error` 之后不再出现新 `tool_call` start 或 assistant text
- Layer 2
  - 断言 Happy 把该轮视为 turn-level failure
  - 断言 backend/session 在失败后仍可继续接收后续 prompt

## Phase-1 Boundary For Human Input / Approval

这是当前 AWorld 与 Happy 接入之间最需要提前说清楚的一条边界。

现有 AWorld CLI 有 `CLIHumanHandler`，默认语义是“在本地终端等待人工输入”。在“不改 Happy”和“不改 `aworld/core`”的约束下，第一阶段 ACP backend 不应该试图偷偷复用这个交互模型。

因此第一阶段建议明确：

- 不桥接复杂 human-in-loop / approval UI
- 如果运行时进入必须等待人工输入的分支，ACP backend 返回明确的 terminal unsupported / requires-human error
- 不允许 backend 在 worker host 的隐藏终端里阻塞等待输入
- 不实现 ACP permission request 流程

### Recommended terminal error model for phase 1

这里还需要再精确一步：AWorld phase 1 不应把 human-in-loop 问题处理成“backend 整体坏了”，而应处理成“当前 turn 无法继续”。

推荐模型：

- human-in-loop / approval 分支触发时：
  - 让当前 `prompt` 以 terminal error 结束
  - 错误 detail 使用稳定、可诊断的 host-owned code family，例如：
    - `AWORLD_ACP_REQUIRES_HUMAN`
    - `AWORLD_ACP_APPROVAL_UNSUPPORTED`
- backend 进程本身保持健康
- session 仍可在后续继续接收新的 `prompt`

不推荐的处理方式：

- 让 backend 进程退出
- 发出 backend-wide `status=stopped`
- 把它伪装成 `cancelled`
- 把它伪装成普通 assistant 文本输出

之所以要这样定义，是因为：

- Happy 把 backend-level `error/stopped` 看成更接近宿主故障，而不是单 turn 失败
- 当前约束下，human-in-loop 未桥接只是 phase-1 capability boundary，不等于 ACP host 自身不可用
- 只有把它建模成 turn-level terminal error，后续 Stage 2/3 才能平滑升级成真正的人机协同/approval 语义

### Error-shape discipline

为了让后续实现和验证有一致标准，phase 1 的 human-in-loop terminal error 还应遵守：

- 错误 detail 应可被日志和测试稳定匹配
- 错误 code 应泛化命名，不带 `happy_*`
- 错误 detail 可以附带简短说明，例如“human approval flow is not bridged in phase 1”
- 不要求 phase 1 定义完整错误枚举体系，但至少要为 human-in-loop unsupported 场景固定一个稳定 family

### Recommended phase-1 error code family

为了让后续实现、测试和 Happy 侧日志都能稳定匹配，建议 phase 1 至少冻结下面这组 host-owned error code family。

| Code | When to use | Scope |
| --- | --- | --- |
| `AWORLD_ACP_SESSION_NOT_FOUND` | `prompt` / `cancel` / later `loadSession` 命中不存在 session | protocol/session |
| `AWORLD_ACP_SESSION_BUSY` | session 仍处于 `running` 或 `cancelling`，新 `prompt` 被拒绝 | turn admission |
| `AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT` | 收到 phase-1 不支持且无法稳定收敛的 prompt block | prompt normalization |
| `AWORLD_ACP_REQUIRES_HUMAN` | 进入一般 human input 分支 | turn terminal |
| `AWORLD_ACP_APPROVAL_UNSUPPORTED` | 进入 approval / permission 分支，但 phase 1 未桥接 | turn terminal |

这个集合刻意保持很小：

- 只覆盖 phase 1 已经明确进入边界的失败面
- 不提前扩成完整错误 taxonomy
- 不把 Happy 私有术语写进主实现错误码

### Recommended phase-1 error detail shape

第一阶段不强制要求完整异常对象体系，但建议错误 detail 至少具备以下可稳定匹配字段：

```json
{
  "code": "AWORLD_ACP_REQUIRES_HUMAN",
  "message": "Human approval/input flow is not bridged in phase 1.",
  "retryable": true
}
```

字段约束建议：

- `code`
  - 必填
  - 使用全大写泛化命名
- `message`
  - 必填
  - 面向人类可读，但内容应稳定，不要把瞬时堆栈文本直接塞进去
- `retryable`
  - 可选，但建议保留
  - 用于区分“改个输入后可重试”与“宿主当前不可用”
- `data`
  - 可选
  - 仅用于附加 machine-checkable 补充字段，例如 `sessionId`、`turnId`、`origin`
  - 不要求承载原始堆栈或大块 runtime payload

补充纪律：

- backend-wide 启动失败可以继续沿用更通用的错误 detail，但不应复用这些 turn-level code
- turn-level terminal error 应优先使用上述 family，而不是临时拼字符串
- 测试和验证层应按 `code` 断言，而不是按整段 message 模糊匹配

这同样只是阶段约束，不是最终结论。若 Happy 某些完整能力面最终依赖 approval / permission 语义，则应在 AWorld 后续阶段补齐相应宿主层协议。

Why:

- 否则 Happy 端侧会看到 turn 卡死，但没有真实可操作的审批面
- 这类能力如果后续要补，应该作为单独 phase 设计协议与宿主行为，而不是在 MVP 里模糊处理

## Borrowed Patterns From OpenClaw

本方案只借鉴 OpenClaw ACP 中与 AWorld 目标直接相关的几个模式：

- 用一个很薄的 `ACP over stdio` host 作为 IDE / controller 接入面
- 用显式 session store 做 `ACP session -> backend session` 映射
- 用独立 translator / mapper 做 runtime event 到 ACP event 的转换
- 把取消、日志、安全边界留在宿主层，而不是下沉到 agent framework 内核

本方案不借鉴的部分：

- 不把 AWorld 设计成 OpenClaw Gateway 风格的产品结构
- 不把 Gateway session key、远程控制面、线程模型原样搬进 AWorld
- 不扩大到 IDE 集成、权限系统、acpx/runtime matrix 等额外范围

## Validation Strategy

验证需要显式拆成两层，而不是一上来就只做 Happy 全链路。

借鉴 OpenClaw 的方式，先证明 bridge / host 自身正确，再证明上层控制面接入正确。对 AWorld 来说，推荐拆成以下两部分。

### Layer 1: AWorld ACP host self-validation

目标：

- 在不依赖 Happy 的情况下，先验证 `aworld-cli acp` 自己是否是一个正确、稳定的 ACP host
- 尽早发现协议映射、session 管理、turn/cancel 语义问题
- 避免每次调试都必须经过 Happy Server / daemon / app 整条链路

借鉴 OpenClaw 的点：

- 可以提供一个很薄的本地 ACP debug client / harness
- 它不属于最终产品能力，只用于开发和测试
- 作用类似 OpenClaw 的 `acp client`：启动本地 ACP host，发送 prompt，观察 session updates 和终态

对 AWorld 的建议验证面：

- protocol smoke:
  - `initialize`
  - `newSession`
  - `prompt`
  - `cancel`
- session tests:
  - session record 创建
  - active turn 跟踪
  - no-op cancel
  - busy prompt rejection
- event-mapper tests:
  - text chunk -> `agent_message_chunk`
  - tool start/end -> `tool_call` / `tool_call_update`
  - final-only output -> final-text fallback
  - optional thinking passthrough
- process boundary tests:
  - `stdout` 只输出 ACP frames
  - `stderr` 只输出诊断日志
  - backend 启动失败、超时、异常退出时返回可诊断错误

建议提供一个最薄的本地验证入口，例如：

- `aworld-cli acp self-test`
- 或单独的 ACP debug client / harness

其目标不是产品化，而是降低 AWorld ACP host 的调试成本。

这一层通过后，才能说明“AWorld ACP 能力本身成立”。

### Layer 2: Happy-integrated end-to-end validation

目标：

- 验证 Happy CLI/daemon 作为 host role 接入 AWorld ACP 后，全链路行为正确
- 验证 Happy 端侧已有能力不会因为 AWorld backend 接入而失效
- 验证同机部署和分布式部署都能成立

这一层关注的问题与 Layer 1 不同：

- Happy 是否能成功拉起 `aworld-cli acp`
- Happy 的 `AcpBackend` 是否正确消费 AWorld 发出的 session updates
- Happy UI / session model 是否能正确表现 AWorld 的 turn、文本、tool、cancel
- Happy 现有能力面是否仍可继续工作，尤其是语音链路不应被 AWorld backend 集成破坏

建议的全链路验证面：

- deployment topology:
  - same-host
  - distributed Happy Server / worker host
- session lifecycle:
  - start session
  - send prompt
  - receive text/tool updates
  - cancel running turn
- capability preservation:
  - 普通文本会话不回退
  - Happy 自己的语音入口仍可工作，并能把会话控制落到 AWorld backend

对语音验证的具体含义需要收敛：

- 不要求 AWorld 在这一层验证 ElevenLabs 或语音 provider 本身
- 要验证的是 Happy 语音链路最终是否还能把 session-level prompt/control 正确落到 AWorld backend
- 也就是验证“Happy voice routing + Happy session routing + AWorld backend”这条结合面，而不是重新测试 Happy 语音基础设施本身

### Layer 3: Capability-preservation validation

当 Layer 1 和 Layer 2 稳定后，还需要一个更贴近最终目标的能力保留验证层。

它关注的问题不是“能不能跑”，而是“接入 AWorld 后，Happy 原有能力是不是仍然成立”。

建议关注：

- voice-driven session send 仍能工作
- session focus / routing 切换不会把请求送错 backend session
- Happy 现有会话体验不会因 AWorld backend 接入而退化成仅文本壳子

这一层可以比 Layer 2 更少，但必须覆盖最关键的能力保留断言。

### Why the split matters

如果不拆层，验证会有两个问题：

- 一旦全链路失败，很难判断是 AWorld ACP host 本身有问题，还是 Happy 接入层有问题
- 每一个 ACP 细节问题都要通过 Happy 整套链路复现，调试效率会非常差

因此应明确顺序：

1. 先通过 AWorld ACP host self-validation，证明 AWorld ACP 能力成立。
2. 再通过 Happy end-to-end validation，证明 AWorld 接入 Happy 体系后的完整链路成立。

这不是缩小最终目标，而是把最终目标拆成可证明的两段。

## Validation Matrix

为了让验证真正可执行，建议把每层验证再映射到更具体的测试类型和产物。

### Matrix A: AWorld ACP host self-validation

建议包含三类测试：

- unit:
  - `session_store.py`
  - `turn_controller.py`
  - `event_mapper.py`
- integration:
  - `server.py` + `runtime_adapter.py` 的进程内联调
  - `prompt -> text/tool/cancel` 行为
- local harness smoke:
  - 使用 `debug_client.py` 或 `self-test` 命令
  - 真正通过 stdio 跑一次 ACP host

建议覆盖的断言：

- one session / one active turn
- busy prompt rejection
- cancel no-op
- cancel race tolerance
- final-text fallback
- stable `toolCallId`
- stdout/stderr 边界

### Matrix B: Happy-integrated end-to-end validation

建议包含两类测试：

- controlled integration test:
  - Happy CLI/daemon 拉起 `aworld-cli acp`
  - 发送单轮 prompt
  - 校验文本、tool、cancel 主链路
- deployment smoke:
  - same-host topology
  - distributed topology

建议覆盖的断言：

- Happy 能成功启动 AWorld backend
- Happy 能正确消费 `agent_message_chunk`
- Happy 能正确闭合 `tool_call` / `tool_call_update`
- Happy cancel 不会把 AWorld session 留在不可恢复状态

### Matrix C: Capability-preservation validation

这一层更接近最终目标，需要更少但更关键的断言。

建议至少覆盖：

- Happy 文本会话体验不回退
- Happy voice-driven session routing 仍然有效
- session focus 切换不会把请求送错到错误的 AWorld session

注意：

- 这里验证的是“Happy 既有能力在接入 AWorld backend 后是否仍成立”
- 不是重新验证 Happy 自己全部语音基础设施

### Suggested test artifact layout

为了保持命名泛化，建议测试目录也按“能力层”而不是“Happy 产品名”组织：

```text
tests/
  acp/
    test_session_store.py
    test_turn_controller.py
    test_event_mapper.py
    test_stdio_server.py
    test_self_test_harness.py
  integration/
    test_acp_happy_host_smoke.py
    test_acp_happy_distributed_smoke.py
    test_acp_capability_preservation.py
```

这里允许在集成测试文件名中出现 `happy`，因为这些文件属于验证层，不属于主实现命名空间。

## Validation Gate Sequence

前面的验证矩阵说明了“测什么”，这里进一步固定“按什么门禁顺序推进”。这能避免实现还没收敛时，就把大量时间花在 Happy 全链路环境问题上。

### Gate 0: Design freeze gate

进入代码实现前，应先冻结以下内容：

- ACP method 最小集合
- turn busy/cancel 精确定义
- tool/thinking/final-text fallback 规则
- 第一阶段 file touch-point map
- plugin/hook bootstrap 目标方案与 fallback
- Layer 1 / Layer 2 / Layer 3 的通过标准

如果其中任一项还在摇摆，不应开始大面积实现。

### Gate 1: Host correctness gate

通过标准：

- `aworld-cli acp` 能被本地 harness 以 stdio 方式拉起
- `initialize/newSession/prompt/cancel` 行为稳定
- busy rejection、cancel race、final-text fallback 已有自动化断言
- `stdout` / `stderr` 边界稳定

失败含义：

- 此时问题仍应被判定为 AWorld ACP host 问题，而不是 Happy 集成问题

### Gate 2: Host-to-Happy contract gate

通过标准：

- Happy CLI/daemon 能稳定拉起 `aworld-cli acp`
- 文本、tool、cancel 三条主链路都已验证
- same-host 与 distributed 两种拓扑都至少各通过一次 smoke

失败含义：

- 此时优先检查 ACP contract、事件时序、部署假设，而不是继续扩 ACP 能力面

### Gate 3: Capability-preservation gate

通过标准：

- Happy 文本会话体验未退化
- Happy 语音路由到 AWorld backend 的控制面结合已被验证
- session focus / routing 不会把请求发错到错误 session

失败含义：

- 说明接入已“可用”但尚未满足最终目标，不应误报为整体方案完成

### Why the gate sequence is mandatory

这是当前方案里必须坚持的纪律，而不是建议项：

- 不先过 Gate 1，就不要把问题归因到 Happy
- 不先过 Gate 2，就不要宣称 AWorld 已经被 Happy 完整接入
- 不先过 Gate 3，就不要宣称 Happy 全量能力已经被保留

## Risks

### Risk: AWorld CLI output may not map one-to-one to the desired ACP UX

说明：

- 现有 AWorld CLI 输出更偏向本地终端渲染，而不是协议事件设计。
- 第一阶段需要接受“端侧可视化能力先跑通、后细化”的节奏。

缓解：

- 限定 MVP 事件子集
- 先保证 turn/text/tool 基本链路完整
- 高级可视化能力延后

### Risk: Avoiding aworld/core changes limits deeply integrated approval semantics

说明：

- 不改 `aworld/core` 会限制某些“真正 runtime 级挂起/恢复”能力的精细度。

缓解：

- 第一阶段不把复杂 approval 作为阻塞项
- 先评估 Happy 端侧控制的主链路价值，再决定是否值得单独规划 framework 级能力

## Open Questions For Follow-up Discussion

- AWorld ACP backend 的具体宿主命令名和命令面应该是什么？
- Happy ACP runner 实际依赖的最小协议/事件集合需要精确到什么级别？
- 首版是否需要支持 thinking，可否先只做 text + tool + status？
- file/artifact 是否先只输出本地路径文本，还是第一版就要做可访问链接桥接？
- cancel / abort 在现有 CLI/executor 外层能做到多强的一致性保证？
