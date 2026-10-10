# self-evolve 后期核查：供 RSI 报告写作用

核查日期：2026-09-18。只读检查了原始成功报告、回归证据、候选包、源码及 git 提交、已有离线验证文件；没有重跑优化、模型或测试。历史任务文本及 `.agent/progress.md` 只用于找到证据，不把 assistant 的总结视为原始运行事实。

## 1. 可以直接下结论的最终结果

成功报告：`/Users/wuman/Documents/workspace/aworld/.aworld/self_evolve/campaign-9463c6a408362f33e443-cycle-002/report.json`。

- `status=succeeded`，`selected_candidate_id=llm-mutator-cb9b242e4400`；31 个顶层 `gate_results` 全为 passed。
- `acceptance_confidence.confidence=verified`，`post_apply.status=accepted`；隔离产物是同目录 `verified_targets/agent-browser/SKILL.md`，`published=false`，源 skill 保持不变。
- `/Users/wuman/Documents/workspace/aworld/tmp/self-evolve-v19-acceptance-proof-20260918.json` 记录严格 verifier exit 0、接受 journal、隔离 registry 加载、源 skill/trajectory/judge 的输入指纹一致。该文件是后验审计记录，成功枚举、门禁、分数和独立性还可直接对照原始 report / regression evidence。
- v19 在第 2/6 个 cycle、2/12 个正式候选内完成。不要把这写成“19轮以内必成功”：v19 是这段工程验收的运行标签，并非有控制的成功率实验。

评分须保留测量单位：

| 检查 | 基线 | 候选 | 差值 | 报告中的 95% 区间 | 实際规模 |
|---|---:|---:|---:|---|---|
| 主质量评分 validation | 86.6667 | 89.4667 | +2.8000 | [0.1695, 5.4305] | 4 个独立案例，各 3 次 judge，12 个配对评分 |
| 标准回归 | 89.7667 | 93.6000 | +3.8333 | [1.3368, 6.3299] | 2 个独立案例，各 3 次 judge，6 个配对评分 |
| challenger 回归 | 91.9667 | 92.5000 | +0.5333 | [-1.4901, 2.5568] | 2 个独立案例，各 3 次 judge，6 个配对评分 |

规模证据在 report 的 `iterations[0].baseline_metrics / candidate_metrics / held_out_metrics`：`effective_case_count=4`、`judge_repetitions=3`，各有 12 个 `score_samples`。回归证据的每个 `suite_results[*].baseline_summary.metrics / candidate_summary.metrics` 为 2、3、6。held_out 同为 4 个独立案例各评 3 次；候选汇总分为 85.8167。最终 evaluator `report.json` 文件展示的是最后一次 judge，不应直接用它覆盖三次汇总分。

11/11 是正式配对回放完成的任务数，**不是主评分中的 11 个独立案例，更不是 12 个独立案例**。主评分区间是当前 gate 的 `paired_standard_error`，重复 judge 评分不能等同于增加了独立任务。

主门禁的 `cost_latency_regression.details` 记录 replay token +1.4271%、latency -9.3292%，均在当前策略之内。challenger 通过码是 `score_improvement_paired_noninferior`，理由是正点估计通过实用非劣界；区间跨 0，不能声称显著提升。

## 2. 最终成功候选究竟改了什么

可直接比对：

- 源：`/Users/wuman/Documents/workspace/aworld/aworld-skills/agent-browser/`
- 最终：`/Users/wuman/Documents/workspace/aworld/.aworld/self_evolve/campaign-9463c6a408362f33e443-cycle-002/verified_targets/agent-browser/`
- 父代候选：`/Users/wuman/Documents/workspace/aworld/.aworld/self_evolve/campaign-9463c6a408362f33e443-cycle-001/candidates/llm-mutator-6ac4892bd886.json`
- 成功候选：`/Users/wuman/Documents/workspace/aworld/.aworld/self_evolve/campaign-9463c6a408362f33e443-cycle-002/candidates/llm-mutator-cb9b242e4400.json`

最清楚的三个行为变化是：

1. 在网页内容读取前先用 scoped snapshot 缩小区域，用 `get count` 判断规模，再逐个元素 `get text`；DOM 变化后刷新引用，文本异常时检查遮挡。源 skill 只有通用命令和基本四步流程，最终增加了这段行为指引。
2. 明确“读取摘要、元数据或某一章节，只能报告这些内容的覆盖面”，未读取全文不能说 `fully covered`；由摘要或先前对话支持的断言必须限定来源。这正对应多轮中“只有摘要却断言论文没有某种方法、已经完全覆盖”的真实质量失败。
3. 删除未核实的细节，不把貌似合理的推断写成来源事实。成功子代还把父代较长的抽取教程收短，集中于抽取步骤与回答约束。

最重要的归因边界：成功子代 `parent_candidate_ids` 精确指向 6ac；两个候选 JSON 中 `replay/capability.json`、`replay/compiler.py`、`replay/runtime.py` 的完整 file delta 记录逐字相同。因此 **v19 cycle001→cycle002 冻结了三个支撑文件，实际改的是 SKILL 抽取章节**。相对最初源 skill，最终包确实还含 compiler/runtime 的重写；capability JSON 只重排/压缩格式而语义相同。不能将本轮 +2.8 归因于新改 runtime，更不能把几个文档规则分别解释为各自贡献了多少分；没有消融实验。

## 3. 后期事故与修复逻辑

### 失败反馈错投 split，模型在修已经通过的部分

已有离线重建 `/Users/wuman/Documents/workspace/aworld-self-evolve-history-focus-20260918/tmp/history-focus/offline-verification.json` 显示：真实 validation 的 evidence_quality 失败，held_out 无失败；旧 focus 却是 held_out，修复后变为 validation；修复包只给失败 split，另一 split 留作观察。反馈 12,723 / 16,000 字符，10 条其他反馈保持不变。提交 `847b60131` 处理跨 cycle，`f1723584b` 扩展至同 cycle 中 failed gates、诊断、约束和 source package 的一致投影。

同目录 `intra-cycle-verification.json` 明确限制：它由 48 个历史 judge 输出离线重建，缺原运行的部分 runtime/replay 元数据，不能称为正式整轮门禁的完整复现。报告应写“反馈来源校正已有离线证据”，不要写成重新运行整轮成功。

可提炼：评价结果不能只有一句“失败”，应带候选、split、gate、来源和修复权限；否则真实进展会被下一轮覆盖。

### 日志里已有报错，模型却只收到泛化的失败码

启动失败的离线验证文件：`/Users/wuman/Documents/workspace/aworld-self-evolve-conformance-stderr-20260918/tmp/conformance-stderr/verification.json`。其中记录的实际报错是 `TypeError: ReplayHandler._write_trace() takes 5 positional arguments but 6 were given`；真实旧 INPUT 与旧重建都未含该原因，修复重建后出现。typed event 和 fingerprint 不变；只增强 `repair_focus` / `validation_feedback`。提交 `89b54af23`。

编译失败验证文件：`/Users/wuman/Documents/workspace/aworld-self-evolve-compile-feedback-20260918/tmp/compile-feedback/verification.json`。两例都显示 fixtures 5/5 唯一，services 却 5/1 唯一；真实错误是 `duplicate replay service id: service-requirement-`，旧反馈未包含具体原因，因此模型仍可能围绕已经修好的 fixtures 操作。提交 `c6ef41c7d` 把具体原因经两个编译 wrapper 和普通 probe 路径传给模型，两个实际 INPUT 各只增 204 字符。

实现约束可直接看 `/Users/wuman/Documents/workspace/aworld/aworld/self_evolve/replay_adaptation_diagnostics.py:34`：描述性 `reason` 限 240 字符、`error_type` 限 80 字符，绑定既有 typed event，而不重写归因。启动 stderr 限定可信 run root、当前 group、扫描/读取量；诊断自身不能覆盖原始异常。

证据留存限制：本次检查上述 conformance 12 个、compile 7 个原生源文件路径均已不存在，验证 JSON 和源码提交仍在。因此这些事故有当时保存的指纹与重建记录，**本次不能声称重新核过它们的原始 stderr/result 字节**。

可提炼：先检查反馈链的信息损失，再认定生成模型不会修；更大的上下文并不能代替正确的诊断传递。

### 独立回归失败，却把主评分意见投给修复器

`/Users/wuman/Documents/workspace/aworld-self-evolve-regression-feedback-20260918/tmp/regression-feedback/verification.json` 保存的真实输入 A/B：标准 suite 89.8333→83.1667，challenger 92.2667→91.4667；旧 focus=`historical_repair`，修复后=`regression`。原来的 suite ID、分数、判定、各臂最多三条原始 issue 被带回；主评与 held_out 作为次要观察保留。提交 `3511b9796`。三例反馈均仍在 16,000 字符以内。

混合故障进一步说明 global 汇总不足以调度：`/Users/wuman/Documents/workspace/aworld-self-evolve-mixed-regression-20260918/tmp/mixed-regression/verification.json` 中，一个 suite 没有 judge signal、0/0、基础设施不可修复；另一个 suite 94.4333→93.4333，有新鲜 judge 证据、候选归属的未决问题。旧 global framework 标签使 focus 倒退到旧语法候选；修复后仅凭同 suite 的 fresh/judged/candidate-owned/repairable 证据恢复当前候选修复前沿，同时保留基础设施前沿和 runtime 冻结。提交 `1d5d220d9`。

本次 regression-feedback 的 6 个原生 source 文件亦已清理；这里引用保留的离线验证记录，而不是声称重新读到原始 suite 报告。

可提炼：一次运行可能同时包含候选问题与评估环境问题；保留各自证据和处理路径，比给整轮贴一个单一错误标签可靠。

### 历史不是越多越好：12 条预算被首个旧报告吃完

`/Users/wuman/Documents/workspace/aworld-self-evolve-history-window-20260918/tmp/history-window/verification.json` 中 before 加载 12 条，全部来自 cycle001，focus 是 7abd/held_out。after 同样加载 12 条，但来自 cycle003、005、001，focus 变为 bfdf/validation。修复用轮转分配固定容量，同时调整 champion 排序，让已选择子代的有效通过检查点不会被旧 repair_focus 字段压下去。提交 `b8157cee9`。

本次重算该文件列出的 18 个原生文件 SHA-256，18/18 仍一致。这项历史窗口事故可直接复核。

此前另一层错误由 `65bfe7351` 修复：`/Users/wuman/Documents/workspace/aworld-self-evolve-frontier-order-20260918/tmp/frontier-order/verification.json` 四个 INPUT 的 focus 都从旧 a370/held_out 改为其通过 held_out 的直接子代 536/validation；保留原始分数/issues/gates和三个 frozen support。不能仅凭“新”取代旧，要求直接血缘、相同 baseline/backend/panel 与显式通过检查点。

可提炼：记忆调度也是搜索算法的一部分。系统需要记住“哪些困难已经解决”，并确保这些检查点在预算内仍能进入下一轮输入。

### 超时和磁盘不能一律归给候选

可直接读的 host ENOSPC 归因源码：`/Users/wuman/Documents/workspace/aworld/aworld/self_evolve/replay_adaptation_diagnostics.py:79`；提交 `9d069598f`。只有 host 原生 `OSError` 且 `errno==ENOSPC` 才产生 infrastructure / shared_run / repairable=false。相同文本、子进程 stderr、包装后的字符串不能获此归因。这样候选不能通过输出“磁盘满”取得基础设施豁免，真正 host 磁盘满也不会浪费下一轮候选修改。

超时修复提交 `b4031fdc6` 保留未知归因，`73587e024` 采用 monotonic clock 的任务期限。progress 中的 v18 主机休眠调查只能作为进一步索引，本次未重新核原 pmset 日志；不要把“休眠是所有慢请求的原因”当作结论。

可提炼：故障 owner 应来自能够证明因果的类型和执行阶段；文案相似不足以决定谁应该修改什么。

### 噪声只能用有界追加证据处理

提交 `4af992283` 为独立 suite 增加最多一次统计复评。当前实现入口：`/Users/wuman/Documents/workspace/aworld/aworld/self_evolve/controllers/run_regression_execution.py:385`；只有 verified apply、score inconclusive 且 tiebreak_eligible，其他 gates 全通过时允许。`...:477` 限定一次、冻结同一候选和回放 panel；之后合并兼容的全部原始评分，而非挑较高的一轮；健康、cost、身份/面板/顺序不符则拒绝。预算预留追加评审，异常后缺失使用量按 unknown 保留而非计零。

但 **最终成功 v19 两个 suite 没有使用额外 tie-break**：原生 suite 只有 baseline/candidate summaries，无 `evaluation_summaries` 追加轮；审计 proof 的 `extra_tiebreak_used=false`、`cross_round_pooling_used=false`。不要写成“补一次复评后 v19 得以通过”；它修的是系统面对未决证据时的通道，不是最终分数的已证原因。

## 4. 这轮的时间主要花在哪

以下取自成功 cycle002 原始 report `budget.debits`，这些条目 `actual_completeness.wall_seconds=true`；只描述该 cycle，不代表全部19次尝试的成本：

| 阶段 | 已记录墙钟时间 |
|---|---:|
| 候选生成 | 23.74 秒 |
| screening | 227.63 秒，约 3 分 48 秒 |
| 正式 paired replay | 2766.59 秒，约 46 分 7 秒 |
| 标准回归 replay | 222.71 秒，约 3 分 43 秒 |
| challenger replay | 217.96 秒，约 3 分 38 秒 |
| evaluation 汇总阶段 | 544.71 秒，约 9 分 5 秒 |

不要把 budget judge 条目的 `20520` 秒当作实测：该条 `actual_completeness.wall_seconds=false`，source 是不完整 judge 遥测的 lower-bound/预算结算；美元同样没有完整实际遥测。生成只用几十秒，回放与判断用几十分钟，能支撑“应优先提高每轮可测量性与反馈命中率”的工程判断，不能据此编造准确总账单。

## 5. 对 RSI 必须保留的结论边界

1. 这是一个用户指定 skill 与历史轨迹上的成功隔离验收案例，未发布到源 skill；不足以证明多任务迁移、递归加速或自我改进普遍收敛。
2. 原生 `/.../cycle-002/regression/evidence/llm-mutator-cb9b242e4400.json` 明确：`data_independent=true`、`execution_independent=true`、`implementation_independent=false`，selection/regression 使用同一 evaluator backend。不能简称“完全独立评估”。
3. 当前 score gate 有统计及非劣两种通过路径；challenger 的区间跨零必须保留。
4. evidence_quality 使用 baseline_relative。最终 held_out gate 虽通过，仍有 `evidence_incomplete=true` 和一条 `support_incomplete`，只是未相对基线退化。“全部 gates 通过”不等于回答没有任何证据缺口。
5. artifact shadow 测量尚未获得主效应：`/Users/wuman/Documents/workspace/aworld/.aworld/self_evolve/campaign-9463c6a408362f33e443-cycle-002/experiments/experiment-ab25b6336c7c30ea06f9092a3d942365/experiment.json` 的 `swap_axis=artifact`、`primary_metric=score`；generator/scheduler/evaluator等身份被冻结。对应 `attribution_report.json` 是 mode=shadow、policy_authoritative=false、effect=null、reason=primary_effect_unavailable，search budget incomplete、无 transfer panel。其 secondary task_success 差为0；latency/token 的独立案例 bootstrap 区间均跨0。这是另一种 artifact 因果测量视图缺证据，**既不能用它否认正式 score gate 已通过，也不能直接把它当成 RSI 元优化能力指标**。
6. 没有消融或跨轮同预算对照，可说修复了可观察缺陷，并最终跑通一个受约束的改进闭环；不能声称每项框架修复造成了多少质量增益。

适合报告的核心判断：这次经验更强地证明了“让失败成为可定位、可继承、可复核的反馈需要大量系统工程”，而不是“给模型一个自我优化目标就会持续变强”。成功结果与这些边界可以同时成立。
