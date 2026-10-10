# 9 月 7—15 日：带数据的早中期尝试与 RSI 推导

本轮只读研究，没有启动优化或执行测试。这里不使用测试通过数作为效果指标，也不重复“12 条历史”和“46 行规则”案例。

证据分三级：A 为现在仍能直接读取的原始日志/launch 产物；B 为原始 report 的命令输出，保存在当时 Codex 任务记录中，本次重新提取，原 report 文件已被清理；C 为当时的工作记录，提交差异可核验实现，但现场数字未重新测量。B 级输出保存在 [early-primary-output-excerpts.json](/Users/wuman/Documents/workspace/aworld/tmp/rsi-report-research/early-primary-output-excerpts.json)，每项保留原命令、工具事件 ID、输出和截断标记。

## 1. 25 个生成尝试槽，0 个权威候选：验证器在定义实际搜索空间

**直接数据，A 级。** 9 月 11 日的 `campaign-5864c4f3f5180a34d80d` 在三个 cycle 中，日志记录的 `candidate attempt slots` 依次推进到 11、7、7，合计 25 个槽次。最终状态仍是 cycle **3/6**、authoritative candidates **0/12**，原因是 `evaluation_support_composition_stalled`。前两轮都出现“同一个 typed conformance violation 经 **3 次 focused repair** 仍存在”而停止生成。

- 第 1 轮最终槽号 11：[原始日志第 21 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:21)。
- 第 2 轮最终槽号 7：[第 46 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:46)。
- 第 3 轮最终槽号 7：[第 67 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:67)。
- 3/6、0/12 及终止原因：[第 75 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:75)。
- 连续 3 次修复未过：[第 24 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:24)、[第 49 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:49)。

**口径必须保留。** 25 是日志宣布开启的生成尝试槽编号之和；不是 25 次已完成 LLM 调用，不是 25 个成功物化候选，也不是仅将配置上限相加。第 1 轮日志从初始 population 之后开始，但后续槽编号连续推进至 11，另两轮有完整的 1 起始编号。

**修复与后续观察。** 工作记录将阻塞定位到 sidecar 读取的数据流写法：从 index 经 records、record 取 value，逻辑分散在分支赋值与后续循环，有界 AST proof 无法完成证明。`3eb5362fe`（9 月 11 日 10:46）改成分支内直接投影，仅修改 runtime 与对应测试。相同终端日志里，下一次原命令运行的第 4 批候选跨过 conformance，进入 paired replay：[第 100 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:100)、[第 114 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:114)。

这不是严格单变量 A/B：两次启动的 HEAD 未保存在该日志里，候选也不同。可写“后续运行跨过了此前的入口阻塞，与投影修复的机制一致”，不能写成只改一行就提升了进化成功率。

**可执行推导。** RSI 的搜索空间是“模型能生成、框架能物化、验证器能证明”的交集。连续多次出现同一个 proof failure 时，继续生成更丰富的候选未必有用；应将失败操作及支持的数据流范式反馈给生成器，必要时修正验证器或提供已证明的实现骨架。区分“已证明不满足约束”和“当前验证器无法证明”，但两者都不能绕过门禁直接应用。

**正文可直接使用：**“一次 campaign 的三个 cycle 把生成尝试槽推进了 25 次，却仍是 0/12 个权威候选。瓶颈不在候选评分，而在候选能否进入可测量的搜索空间。”

## 2. 11 例回放，不等于 11 例质量评价：采样覆盖与重复评分是两种投资

**原始报告输出，B 级。** `campaign-971086b9a0a045947a14-cycle-010` 的报告同时记录：

| 口径 | baseline | candidate |
|---|---:|---:|
| deterministic verification case count | 11 | 11 |
| deterministic verification pass count | 0 | 11 |
| validation comparison effective case count | 2 | 2 |
| groundedness | 3.0 | 3.5 |
| efficiency | 3.5 | 4.0 |
| completeness | 4.5 | 4.0 |

同一报告的 held-out effective case count 为 **4**。这些维度分数来自 validation 的 **2 个不同 case**，不应以“11/11 回放”替换其统计口径。`deterministic_verification` 也是报告字段名，不能将其 pass count 直接解释成“11 个用户任务全成功”。

来源：`early-primary-output-excerpts.json` 中 `index: 13`，原命令读取 cycle-010/report.json 的 baseline、candidate、held-out metrics；事件对应 `thread-1-older-messages.json` 所属早期长 turn。`index: 14` 另保留 cycle-005 与 cycle-010 的 evaluator verdict 和 evidence 指标比较。

**后续改变，提交直接可核验。** 9 月 15 日 `b0528e08a` 将 11-case 数据集的 validation 从 `count // 5 = 2` 扩至 **4**，held-out 仍保留 **4**，train 因此从 **5** 变为 **3**。提交注释明确区分任务间差异与对同一输出重复打分。这里是数据分配方案的改变，不是已经测得质量提升的效果量。

**可执行推导。** 每次决定追加评估预算，应先判断不确定性来自任务覆盖不足，还是同一任务的判分波动。增加不同任务能检验泛化，重复评分只能减小部分判分噪声；多次 judge 同两个 case，不会创造新的任务覆盖。三维分数还显示实际取舍：groundedness 和 efficiency 上升时，completeness 下降。修复目标应保留各维度和有效 case 数，而不是只传一个总分。

**边界。** 2→4 的变化支持“扩大覆盖是当时采取的应对”，不能由此证明 4 是最优样本量，也不能只凭两个案例的均分变化断言通用能力提升。

## 3. 同一候选走过 4 个 cycle：轮数不能代表递归进步次数

**直接数据，A 级。** `tmp/aworld-acceptance-tmux.log` 中，`campaign-f8de35d29b99521ee666` 的 cycle-001、002、003、004 都在回放同一个 `llm-mutator-1e98020d8d48`。后 3 轮明确由 `measurement-pending candidate` 恢复，而不是生成了 3 名新候选。

- cycle-001：[第 89 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:89)，本轮经过生成后开始该候选回放。
- cycle-002 恢复：[第 320 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:320)。
- cycle-003 恢复：[第 510 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:510)。
- cycle-004 启动及相同候选：[第 627 行](/Users/wuman/Documents/workspace/aworld/tmp/aworld-acceptance-tmux.log:627)。

另一个早期 campaign，工作记录记载恢复了 **8 个已完成 paired cases**：[.agent/progress.md:765](/Users/wuman/Documents/workspace/aworld/.agent/progress.md:765)，属于 C 级记录。`90ef70b8c` 的代码差异证实曾修复“顶层选中的 repair focus 遮住后来未完成 checkpoint”的恢复路径，要求核对候选 fingerprint、pending cases 与真实磁盘 checkpoint，再继续测量。

**可执行推导。** RSI 的进度应分成新候选数、已经完成的可比测量、复用的证据和测量续跑次数。一次变异可能需要多个调度 cycle 才能获得足够证据；增加 cycle 并不自动增加知识。调度器应优先补完可恢复实验，且不能重复收取新候选名额。

**边界。** 4 轮测同一候选不等于 3 轮都是浪费：其中可能是必要的断点续跑或对失败 baseline 的重新测量。日志不能单独证明每次重测都可避免。主报告建议只使用“4 cycle／同一候选”这一可直接核验对比，不使用日志心跳相加推断总耗时。

## 4. 12 轮、60 个服务启动记录：长跑可靠性需要独立的运行条件证据

**直接产物，A 级。** `tmp/replay-preflight-stress/0` 至 `/11` 保留 **12 轮 × 每轮 5 个服务 = 60 个** `launch.json`。本次只读聚合结果是 **60/60 status=started，error_type 全为 null**，其中 **12 个 http_fixture、48 个 skill_runtime**。http fixture 的 launch command 没有 descendant supervisor，skill runtime 的命令仍保留 supervisor。

可直接读取的样例：

- [第 0 轮 framework fixture](/Users/wuman/Documents/workspace/aworld/tmp/replay-preflight-stress/0/replay_services/svc_requirement-380b35c80ed0ad4259f2/launch.json)。
- [第 0 轮 skill runtime](/Users/wuman/Documents/workspace/aworld/tmp/replay-preflight-stress/0/replay_services/svc_requirement-ba545725190097ec76f4/launch.json)。
- [第 11 轮 framework fixture](/Users/wuman/Documents/workspace/aworld/tmp/replay-preflight-stress/11/replay_services/svc_requirement-380b35c80ed0ad4259f2/launch.json)。

**此前现象，C 级。** 工作记录记载，同一冻结 capability 的 continuation preflight 在首个 framework HTTP fixture 绑定端口前反复超时；宿主重启后立即通过，于是将问题定位到累积的进程压力。`732497c4f` 将不能 fork 的 framework fixture 改为直接在 sandbox/resource-limited 进程组运行；`852fe62e3` 后续补充 parent PID 监控。工作记录还记载连续 12 轮没有残留进程，但 `launch.json` 本身不能证明回收后的零残留，本报告将两者分开。

**可执行推导。** 候选搜索会不断消耗并改变宿主状态，长期运行产生的错误分布不一定与开始时相同。应对固定的冻结 capability 单独做重复 preflight，把服务是否启动、是否回收与候选质量隔离。否则搜索器会把运行条件变差当成候选退化，并修改原本无关的行为。

**边界。** 60 个启动记录不是 60 次完整任务成功，更不是候选能力增益。保留 skill runtime 的 supervisor 说明此处采用了不同来源代码的不同生命周期管理，并没有为通过实验撤掉所有隔离。

## 5. 83 秒后给出答案，当前证据目录仍是 0 字节：可用信息与可验收证据不是同一状态

**原始产物输出，B 级。** 在 `campaign-971086b9a0a045947a14-cycle-011` 的候选 `llm-mutator-d401e28a031a`、任务 `task_20260522105248` 中，原 `metrics.json` 记录延迟 **83,380.48 ms**，`framework_evidence_inventory_file_count=0`、`framework_evidence_inventory_bytes=0`。原 `lifecycle.json` 的失败为 `replay_evidence_production_failed`，owner 为 candidate，stage 为 evidence_finalization。该轮总体 gate 记录 candidate executed **11**、comparable pairs **10**、candidate execution failure **1**。

来源：`early-primary-output-excerpts.json` 的 `index: 8` 保留完整 lifecycle/metrics 输出，`index: 2` 保留总体 gate 字段。原任务中另有 framework_task_response 读取输出，显示 **5 个 action step**：检索本地 PDF、查目录、读取已保存摘录、写 advisory manifest、形成最终回答。这里不用回答自称“证据已确认”来证明内容正确。

工作记录后来明确该成员写入的 advisory manifest 指向当前 evidence namespace 之外，因此最终失败正确归为 candidate-owned：[.agent/progress.md:779](/Users/wuman/Documents/workspace/aworld/.agent/progress.md:779)。早期 commentary 曾将类似现场怀疑为框架物化遗漏，不能沿用那条未证实归因覆盖最终记录。

**可执行推导。** 让模型“看到了来源”只是信息获取；让结果能够在当前候选的隔离环境中被校验，还需要有效工件、来源绑定及正确生命周期。自我改进系统应把检索成功、答案形成、证据闭合和可比实验完成分别记录。否则一段看似完整的回答容易被错误记成已验证收益，或者证据打包错误被错误诊断为推理能力不足。

**边界。** 这里不能说“原答案正确所以 gate 错了”，也不能仅凭 0 字节说“agent 没有读取任何来源”。可以确认的是：当前候选没有形成门禁要求的本地证据库存，导致 11 个已执行 case 只有 10 个可比结果。

## 最值得补入主报告的三条

1. **25 生成尝试槽／0 权威候选**：用作“验证器与候选表示共同决定搜索空间”的实证入口；严格保留槽次单位和非单变量对照边界。
2. **4 个 cycle／同一候选，另有 8 个 paired cases 的恢复记录**：用作“递归积累取决于实验状态继承，cycle 不是进步单位”的实证入口。
3. **12×5 个服务启动记录与此前冻结 capability 的启动失败**：用作“运行条件本身会随长跑变化，需要独立健康证据”的实证入口。

2-case validation 与三维取舍可作为统计设计的补充；83 秒／0 字节／10 个可比结果可作为证据闭合的补充。以上都是具体工程现象支持的技术推导，不能当作通用 RSI 理论已被实验验证。
