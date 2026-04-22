# Proposal

## Why

当前希望复用 Happy 的 App/Web、Server 与 CLI/daemon 体系，为 AWorld 提供一套可自部署的端侧远程控制能力，但存在三个硬约束：

- 不能修改 Happy 代码。
- 不希望修改 `aworld/core`，避免把 Happy 接入需求下沉到 AWorld Agent SDK/framework 内核。
- 既要支持 Happy Server 与 AWorld 在同一台主机部署，也要支持 Happy Server 与 AWorld worker 分布式部署。

在这些约束下，AWorld 不能要求 Happy 学习一个新的 AWorld channel 或专有协议，而必须适配 Happy 已有的 backend 接入面。综合现有能力与复杂度，最合适的方向是由 AWorld 提供一个 ACP-compatible backend，由 Happy 现有的 ACP runner 负责拉起、会话同步和端侧展示。

需要额外强调的是：

- 最终目标不是“做一个够用的 MVP backend 就结束”
- 最终目标是让 AWorld 可以真正接入 Happy 这整套体系，成为 Happy 可控制、可部署、可演进的一类 backend
- 当前文档里讨论的最小方法面、最小事件面，只是第一阶段实现切片，用来控制改造风险，而不是最终能力上限
- 最终集成不得以牺牲 Happy 现有能力为代价；Happy 已有的语音等端侧能力需要继续保留，且不能要求为 AWorld 接入修改 Happy 代码

## What Changes

- 为 AWorld 增加一个以 `aworld-cli` 为宿主、通过 `stdio` 暴露的 ACP-compatible backend 方案，作为 Happy 端侧控制链路的首选接入面。
- 明确该方案的实现边界：
  - 不修改 Happy 代码。
  - 不修改 `aworld/core`。
  - 只在 `aworld-cli` 与必要的 gateway/host 辅助层中扩展。
- 明确该方案不是新增 `aworld_gateway/channels/happy` 的 message channel，而是一个面向 Happy CLI/daemon 的 backend host。
- 明确 `Happy CLI/daemon` 作为 host role 是必选集成边界，但 AWorld 只对接其 generic ACP backend contract，不依赖 Happy 私有 agent 实现细节。
- 明确 AWorld 的 ACP 能力本身是泛化能力；Happy 只是当前用于验证对接的体系，不应把 Happy 语义固化进 AWorld 的代码目录、模块命名或公共命令面。
- 明确在实现时应尽量把 ACP 能力收敛在 `aworld-cli` 的统一目录下，避免对现有 CLI 功能做大范围侵入式修改，降低不必要的回归面。
- 明确在不增加不必要复杂度的前提下，ACP 方案优先复用 AWorld 现有 plugins、hooks 等扩展能力，而不是把相关适配逻辑全部硬编码进 ACP 主流程。
- 明确该方案的长期目标不是只保留 Happy 的文本聊天能力，而是保持 Happy 既有能力面可继续工作，包括语音这类 Happy 原生能力。
- 约束第一阶段实现切片：
  - 先支持 prompt 输入、turn 生命周期、流式文本输出、基础 tool 事件、取消/中止。
  - 先明确 ACP session 到 AWorld host-local session 的映射边界。
  - 第一阶段先不桥接本地终端型 human-in-loop/approval 交互。
  - artifact/file、subagent 可视化、丰富审批语义、以及 Happy 原生语音等能力的完整对齐不从最终目标中删除，但放在后续阶段继续展开。
- 明确两种部署拓扑都应被支持：
  - 同机部署：Happy Server 与 AWorld worker 在同一 host。
  - 分布式部署：Happy Server 独立部署，AWorld worker 上运行 Happy CLI/daemon 与 AWorld backend。
- 为后续深度设计讨论建立单独的 OpenSpec change 承载面，后续继续在该 change 下补充设计、任务与 spec delta。
