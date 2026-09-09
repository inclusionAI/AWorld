# AWorld Terminal Benchmark Rollout 与 Context Management 分析

## 结论摘要

本报告分析 Batch `b-771cbacf3eb9420e8a4e` 中两个 AWorld 模型系列的 89 道 Terminal Benchmark 题目，并直接从每个 run 详情 API 的 `harbor.trajectory` 字段下载数据。该字段就是 Dashboard 的 **Raw trajectory** 区域，而不是 live log、verifier stdout 或页面摘要。

核心结论如下：

1. 89 道题中，44 道两个模型 Reward 都为 0，13 道出现 0/1 分歧，32 道两个模型都为 1。`dsv4_flash` 总成功 40 道，`glm52` 总成功 37 道；13 道分歧题中前者胜 8 道、后者胜 5 道，说明不存在适合所有任务的单一静态模型路由。
2. 目标集合共 120 个 run：双 0 全量 88 个、分歧全量 26 个、随机双 1 正例 6 个。当前服务端可取得 92 条 Raw trajectory，28 条的 `harbor.trajectory` 为空。
3. 92 条 Raw trajectory 与批量 trajectory 下载接口中的对象逐一比较，结果为 **92/92 完全相等**，确认下载内容就是页面 Raw trajectory。
4. 更严重的问题是：双 0 的 70 条可下载 Raw trajectory 中，有 54 条只有 `(AWorld completed without a captured response)` 占位文本，没有真实 LLM/tool/observation 过程。双 0 的 44 道题中，只有 4 道同时拥有两个模型的详细轨迹。因此当前首要问题不是立即从双 0 推断模型策略，而是修复 AWorld→TaskResponse→ATIF→Scheduler 的轨迹保真链路。
5. 在 38 条有真实过程的轨迹中，工具输出和循环成本很高：median observation 为 76,336 字符，p90 为 446,411；上下文驻留代理值 median 为 110 万 char-turns，p90 为 1,983 万；32/38 有错误信号，26/38 有完全重复命令，25/38 至少出现一次超过 8K 字符的 observation，7/38 在任务中临时安装依赖。
6. 正例证明“成功”和“高效”必须分开优化：`portfolio-optimization` 两个模型都得 1 分，但一个只用 8 步、10 次工具调用和 35.7K observation 字符，另一个用了 63 步、66 次调用和 231K 字符。这正适合作为 spec 中 `cost_per_successful_task` 的正向对照。

## 数据范围与方法

- Dashboard：`https://mcpgateway-pre.alipay.com/scheduler/dashboard/batches/b-771cbacf3eb9420e8a4e?env=pre&source=main`
- 分析日期：2026-08-30
- Harness：`aworld`
- 系列 1：`matrixllm.aisearch_duet_dsv4_flash`
- 系列 2：`matrixllm.aisearch_duet_glm52`
- 轨迹协议：ATIF-v1.7
- Raw trajectory 真值来源：`GET /scheduler/api/dashboard/runs/{run_id}` 响应中的 `harbor.trajectory`
- 正例采样：以 batch ID 为固定 seed，从 32 道双 1 题中确定性随机抽取 3 道，每道保留两个模型轨迹。

字符指标不是 provider token 账单。`observation_residency_char_turns` 的计算方式为：每条 observation 的字符数乘以后续 agent turn 数，用于近似“如果完整历史持续重放，该输出会驻留多少轮”。

## Reward 分布

| 类别 | 题目数 | run 数 | 占全部题目 |
|---|---:|---:|---:|
| 两模型均为 0 | 44 | 88 | 49.4% |
| 一个为 0、一个为 1 | 13 | 26 | 14.6% |
| 两模型均为 1 | 32 | 64 | 36.0% |
| 合计 | 89 | 178 | 100% |

模型汇总：

| 模型 | Reward=1 | Reward=0 | 成功率 |
|---|---:|---:|---:|
| `dsv4_flash` | 40 | 49 | 44.9% |
| `glm52` | 37 | 52 | 41.6% |

## Raw trajectory 下载与保真情况

| 数据集 | 请求 run | Raw 可得 | Raw 为空 | 详细过程 | 占位轨迹 |
|---|---:|---:|---:|---:|---:|
| 双 0 全量 | 88 | 70 | 18 | 16 | 54 |
| 0/1 分歧全量 | 26 | 16 | 10 | 16 | 0 |
| 双 1 随机正例 | 6 | 6 | 0 | 6 | 0 |
| 合计 | 120 | 92 | 28 | 38 | 54 |

Raw 数据按以下目录保存：

- `raw-trajectories/both-zero/`
- `raw-trajectories/split-reward/`
- `raw-trajectories/both-one-sample/`

`raw-trajectory-manifest.json` 记录每个 run 的 task、模型系列、Reward、trial ID、服务端 trajectory checksum、本地 SHA-256、schema version、step count 和是否可得。批量下载包保留为交叉验证证据，但后续 Context Management 优化应以 `raw-trajectories/` 和该 manifest 为输入。

### P0 数据问题：失败轨迹缺失存在明显选择偏差

28 条 Raw trajectory 缺失全部发生在双 0 或分歧集合，随机双 1 正例为 6/6 可得。另有 54 条双 0 轨迹虽然 JSON 可得，却只包含“未捕获响应”占位符。结果是：越需要诊断的失败 run，越可能没有足够证据。

这会同时损害：

- Context Management 的失败归因；
- 两模型的首个分歧点分析；
- reward-aware trajectory mining；
- cache、token 和 Tool 输出成本评估；
- 后续训练或策略优化数据的代表性。

因此 ATIF 文件“存在”不能再等价为“轨迹有效”。需要显式输出 `trajectory_fidelity = complete | partial | placeholder | unavailable`，并把完整率作为 benchmark hard gate。

## 详细轨迹的量化结果

以下统计只使用 38 条包含真实 agent/tool/observation 的轨迹，排除了 54 条占位轨迹。

| 指标 | Median | P90 | Max |
|---|---:|---:|---:|
| steps | 29.5 | 84.7 | 128 |
| tool calls | 25.0 | 79.7 | 122 |
| observation 字符 | 76,336 | 446,411 | 874,949 |
| 单次最大 observation 字符 | 10,848 | 28,703 | 309,403 |
| observation residency char-turns | 1,100,005 | 19,834,372 | 46,826,810 |
| 完全重复命令数 | 2.0 | 13.6 | 58 |

事件比例：

- 32/38（84.2%）至少出现一个工具失败或错误文本信号；
- 26/38（68.4%）重复执行过完全相同的命令；
- 25/38（65.8%）至少有一个 observation 超过 8K 字符；
- 21/38（55.3%）出现空 agent message；
- 7/38（18.4%）在任务运行期间执行依赖安装。

错误信号使用启发式识别，包括 `success=false`、非零 return code 和常见错误文本。Shell pipeline 可能掩盖 return code，输出中也可能只是讨论某个错误，因此该指标用于发现热点，不应直接作为精确失败计数。

## 双 0 案例分析

双 0 是最大类别，但也是轨迹质量最差的类别：88 个目标 run 中只有 16 条详细轨迹；44 道题中只有以下 4 道拥有完整的双模型详细对照：

- `cancel-async-tasks`
- `filter-js-from-html`
- `model-extraction-relu-logits`
- `mteb-leaderboard`

在 16 条可分析的双 0 详细轨迹中：

- median 26 步、22 次工具调用；
- median observation 68.9K 字符；
- median context-residency proxy 827.8K char-turns；
- 14/16 有错误信号；
- 12/16 有超过 8K 字符的 observation；
- 8/16 有重复命令；
- 3/16 临时安装依赖。

典型模式：

1. **长循环但没有收敛。** `filter-js-from-html` 的一个 run 达到 72 步、65 次调用、344K observation 和 15 次重复命令，仍为 0 分。框架缺少针对“相同假设—相同命令—相同结果”的语义循环检测，以及触发 checkpoint、换策略或停止的机制。
2. **错误后继续堆积上下文。** `cancel-async-tasks` 的一个 run 在 18 步中检测到 10 个错误信号；错误输出持续进入后续上下文，却没有结构化 error state、恢复预算或回滚点。
3. **运行结束和任务完成被混为一谈。** 多个轨迹最终给出乐观完成声明，但 Reward 为 0。框架需要把 `agent_finished` 与 `deliverable_verified` 分开建模，不能只凭自然语言完成声明结束任务。
4. **证据缺失本身掩盖了主要故障。** 54 条占位轨迹无法判断是 LLM 未调用、TaskResponse 丢失、stream 消费竞态、trajectory export 降级，还是实际执行未发生。应先修采集链路，再对双 0 做大规模因果归因。

## 0/1 分歧案例分析

13 道分歧题如下：

| 题目 | `dsv4_flash` | `glm52` | Raw 详细轨迹 |
|---|---:|---:|---|
| break-filter-js-from-html | 0 | 1 | 双边可得 |
| chess-best-move | 0 | 1 | 仅 `glm52` |
| db-wal-recovery | 1 | 0 | 仅 `dsv4_flash` |
| feal-differential-cryptanalysis | 0 | 1 | 双边可得 |
| large-scale-text-editing | 1 | 0 | 仅 `dsv4_flash` |
| largest-eigenval | 0 | 1 | 双边缺失 |
| llm-inference-batching-scheduler | 1 | 0 | 仅 `dsv4_flash` |
| mteb-retrieve | 1 | 0 | 双边可得 |
| pytorch-model-recovery | 1 | 0 | 仅 `dsv4_flash` |
| rstan-to-pystan | 0 | 1 | 仅 `glm52` |
| sam-cell-seg | 1 | 0 | 双边可得 |
| sanitize-git-repo | 1 | 0 | 双边可得 |
| train-fasttext | 1 | 0 | 双边缺失 |

只有 5 道题具备完整双边详细轨迹，因此下面结论是高价值案例观察，不是统计显著的模型排名：

### `sam-cell-seg`：更短、更少错误、更少噪声的一侧成功

成功侧比失败侧少 47 步、44 次工具调用、2 个错误信号、360K observation 字符、53 个空 agent message 和 2 次运行时依赖安装。两侧最终都声称交付物已验证，但只有更收敛的一侧通过 verifier。该案例直接支持：

- 环境与依赖预检；
- 重复/停滞检测；
- 输出预算和阶段 checkpoint；
- 完成前的真实交付物验证，而非信任 final answer 声明。

### `break-filter-js-from-html`：完成协议比“继续思考”更重要

失败侧最后仍在陈述“重新考虑问题”，没有形成可靠完成证据；成功侧明确写入目标文件并运行测试确认。两侧步数相同，说明简单的 max-step 限制不能解决问题。框架需要 `CompletionContract`，要求在结束前提交目标路径、artifact hash、验证命令及结果。

### `sanitize-git-repo`：工具调用少不等于上下文成本低

成功侧用了 122 次调用，失败侧只用了 31 次，但成功侧 observation 总量反而少约 208K 字符。失败侧曾出现单次 309K 字符 observation。该案例证明应在 Tool adapter 执行时控制输出，而不是只限制调用次数；`structured/head_tail/artifact_stream` 比粗暴 max-turn 更关键。

### `mteb-retrieve`：错误恢复质量优于单纯减少步骤

成功侧比失败侧多 3 步、5 次调用，但少 3 个错误信号、3 次重复命令和 5 个空 message，且 observation 略少。成功不与“最短轨迹”单调相关，预算策略必须允许必要的验证，同时惩罚无信息增益的重复。

## 双 1 正向样本

确定性随机样本为：

- `fix-ocaml-gc`
- `portfolio-optimization`
- `prove-plus-comm`

它们分别代表三种有价值的正例：

1. **高复杂度但最终成功：** `fix-ocaml-gc` 两侧都经历较多步骤和错误，说明不能用统一硬阈值截断所有长任务，需要按任务阶段、信息增益和剩余预算自适应控制。
2. **同成功率下的巨大效率差：** `portfolio-optimization` 中 `glm52` 为 8 步、10 次调用、35.7K observation；`dsv4_flash` 为 63 步、66 次调用、231K observation。它是 `cost_per_successful_task`、compact policy 和模型路由的优先回归样本。
3. **低噪声成功基线：** `prove-plus-comm` 两侧分别为 7/8 步、5/6 次调用和 4.2K/7.2K observation，可用来验证 Context Harness 不会为了复杂治理而伤害本来已很简洁的任务。

## 对现有 Context Management spec 的验证与缺口

现有 `2026-08-17-unified-context-management-harness-design.md` 已正确覆盖以下方向：

- 执行前 `ToolOutputPolicy` 与 inline/offload 上限；
- `context_token_turns` 和 `cost_per_successful_task`；
- task epoch、checkpoint/compact、rewind/fork、resume；
- task-sticky Skill/Tool Catalog 和 cache identity；
- append-only `llm_calls`、真实 request snapshot 和 Context Inspector；
- delegation 的独立 context/budget。

本次 benchmark 说明这些机制应保留，但还需补入以下系统契约：

| 优先级 | 建议加入 spec 的能力 | Benchmark 证据 |
|---|---|---|
| P0 | `TrajectoryFidelity` 与 durable capture contract | 28/120 Raw 为空；54/92 是占位轨迹；失败样本缺失偏置明显 |
| P0 | `CompletionContract` 与 pre-finish artifact verifier | final answer 声称完成不等于 Reward=1；`break-filter-js-from-html` 失败侧未形成完成证据 |
| P0 | Tool 输出策略在 runtime 执行时强制生效 | 单次 observation 最大 309K；总量最大 875K；`sanitize-git-repo` 证明少调用也可更贵 |
| P0 | append-only event/LLM/tool truth source，不依赖最终 TaskResponse | 大量 `(completed without a captured response)` 表明 task-response-only capture 不可靠 |
| P1 | 语义循环与停滞检测器 | 26/38 有重复命令；最大重复 58 次 |
| P1 | capability/environment preflight 与受控依赖 overlay | 7/38 运行时安装依赖；多条轨迹先试错探测环境 |
| P1 | task-family 模型/effort/Skill 路由与自适应升级 | 13 道分歧题中两个模型分别胜 8/5；无单一模型全胜 |
| P1 | success-conditioned efficiency evaluator | `portfolio-optimization` 双成功但成本差约一个数量级 |
| P1 | 版本、token、cache 和 request provenance 完整导出 | 92/92 agent version 为 `unknown`，92/92 final metrics 不含 token usage |
| P2 | 高噪声探索隔离到 child context，并结构化合并 | 长工具链的历史驻留代理值最高 46.8M char-turns |

## 系统框架优化建议

### P0.1 修复轨迹真值与持久化链路

1. 在 LLM/provider 边界和 Tool runtime 边界 append event，不以最终 `TaskResponse.answer` 是否存在决定轨迹是否存在。
2. 每次 LLM call 保存 request ID、task ID、agent ID、request snapshot/hash、usage、finish reason；每次 Tool call 保存 call ID、参数 hash、原始输出 artifact、inline view、return code。
3. ATIF export 只做真值事件的投影，不从 final answer 反向重建过程。
4. 增加 `trajectory_fidelity`、`missing_reason`、`capture_stage`、`event_count`、`llm_call_count`、`tool_call_count`、`checksum`。
5. Batch 完成前校验 run-detail Raw trajectory、批量下载 artifact 与 checksum 三方一致；失败时重试并保留原始 event log。
6. Hard gate 建议：`raw_trajectory_available_rate == 100%`、`placeholder_trajectory_count == 0`、`tool_call_pair_match_rate == 100%`、`llm_call_snapshot_match_rate == 100%`。

代码层面需要重点检查 `EventRunner._save_trajectories()`、CLI `outputs.response()`/`final_result` 归并和 Scheduler ATIF producer 之间的完成竞态。当前 CLI 路径只有在特定 `outputs.is_complete` 状态下等待 `_run_impl_task`，而 benchmark 的占位轨迹由 `task_response` capture mode 产生；这是优先排查方向，但仅凭导出数据还不能断言唯一根因。

### P0.2 引入 CompletionContract

每个任务在首轮解析时生成结构化完成契约：

- required artifact 路径、类型和 schema；
- 不允许修改的输入文件；
- 可执行的本地 smoke test；
- 验证证据的最大新鲜度；
- 最终 answer 必须引用的 artifact hash 和 test result ID。

状态应至少区分 `agent_stopped`、`artifact_present`、`self_check_passed`、`external_verifier_passed`。若交付物缺失或验证证据过期，FINISHED 应转为继续执行、回滚或明确失败，而不是输出完成声明。

### P0.3 落实执行时 ToolOutputPolicy

推荐默认策略：

- 文件枚举、grep、编译和测试输出默认 `structured` 或 `head_tail`；
- 原始 stdout/stderr 全量写 artifact，模型上下文只保留 return code、关键错误、统计、首尾片段和 artifact ref；
- 同一 artifact/content hash 不重复 inline；
- 每个 observation 记录 `raw_bytes`、`inline_tokens`、`offloaded_tokens` 和截断原因；
- shell pipeline 必须保留各阶段真实 exit status，避免 `| tail` 将失败伪装成 return code 0。

Compact 触发条件应同时考虑当前 token、`context_token_turns` 预测值、重复度和 observation 信息增益，而不是只等 context overflow。

### P1.1 循环检测、结构化工作状态与自适应预算

维护一个小型 working state：当前假设、已验证事实、失败原因、已修改 artifact、下一验证动作。对规范化后的 `(tool, target, command, result fingerprint)` 检测重复：

- 第 2 次重复：提醒并要求说明新信息；
- 第 3 次重复：自动 checkpoint，压缩旧输出；
- 再次重复：切换策略、Skill、模型/effort 或终止该分支。

预算按阶段分配给 explore、implement、verify、repair，而不是只给全局 max turns。`fix-ocaml-gc` 表明复杂任务需要较长预算，`sam-cell-seg` 则表明无信息增益的长链应更早收敛。

### P1.2 Capability 和环境预检

首轮 provider call 前生成 capability manifest：可用语言/runtime、依赖版本、GPU/CPU、网络策略、图像/视频输入、测试入口、可写目录。缺少关键能力时：

- 优先路由到原生 Tool/Skill，例如视觉任务使用 image/vision 工具，而不是在 shell 中手工像素推断；
- 依赖安装进入隔离 overlay，记录 lockfile、来源、耗时和安全策略；
- 不允许未经治理的 `--break-system-packages` 影响共享环境；
- 预检结果作为稳定 task context，而不是每轮重复 `which`/`pip list`。

### P1.3 Reward-aware 路由和 paired replay

以 13 道分歧题构建 counterfactual replay：固定环境、工具和输入，对齐两个模型的首个策略分歧、首次错误、artifact 变更和验证动作。路由器使用任务特征选择 model/effort/Skill，并在达到错误预算或停滞阈值时升级。模型路由实验必须与 Context Compiler 实验分开，避免把模型差异误算为 context 收益。

### P1.4 将正例纳入效率门禁

双 1 数据不用于证明质量提升，而用于证明质量不变时的成本下降。建议新增：

- `paired_success_cost_ratio`
- `observation_token_turns_per_success`
- `duplicate_tool_calls_per_success`
- `verification_calls_per_success`
- `artifact_reads_per_success`

优先用 `portfolio-optimization` 检测同成功结果下的成本优化，用 `prove-plus-comm` 检测治理开销回退，用 `fix-ocaml-gc` 检测自适应预算是否误杀复杂成功任务。

## 建议的数据集使用方式

1. **先按 fidelity 过滤。** 训练或因果分析只使用 `detailed`；`placeholder/unavailable` 单独作为 capture-reliability 数据，不作为模型失败策略样本。
2. **双 0 用于失败模式聚类。** 当前只对 16 条详细轨迹做 loop/error/output/completion 聚类；修复采集后重跑 44 道题，再扩展结论。
3. **0/1 用于 paired counterfactual。** 当前 5 个完整 pair 可人工标注首个关键分歧；其余 8 道待补轨迹后加入。
4. **双 1 用于 success-conditioned cost。** 保持 3 道确定性样本，并在后续每次 benchmark 使用相同 seed，避免挑选性报告。
5. **保留原始数据，只派生 sidecar。** 不直接改写 ATIF；将 task/reward/fidelity/metrics/标注写入 manifest 或 sidecar，保证可以重新计算。

## 局限

- Reward 是最终二值结果，不能单独证明失败由 Context Management 导致；模型推理、工具能力、环境、任务理解和 verifier 都可能贡献差异。
- 只有 5 个分歧题和 4 个双 0 题具有完整双边详细轨迹，不能做显著性模型排名。
- observation 字符和 char-turns 是上下文压力代理，不是 provider token 或真实计费。
- 当前 Raw trajectory 未导出 prompt/output/reasoning/cache token，agent version 全为 `unknown`，无法计算现有 spec 要求的真实 cache-adjusted cost。
- 正例只有 3 道、6 个 run，用于框架回归输入，不代表整个双 1 分布。

## 产物索引

- `selection.json`：题目分组、Reward 和 run ID 真值
- `raw-trajectories/`：直接来自 Dashboard Raw trajectory 区域的数据
- `raw-trajectory-manifest.json`：逐 run 下载、校验和保真元数据
- `trajectory-metrics.json`：逐轨迹指标、分类统计和 paired metrics
- `task-report.json`：Batch task-report 原始响应
- `artifact-manifests.json`：批量下载接口 manifest
- `download-summary.json`：分类下载覆盖率
- `*-trajectories-part-*.zip`：服务端批量下载原始 ZIP，用于与 run-detail Raw trajectory 交叉校验
