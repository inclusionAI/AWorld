# 整周复盘：模型输入与测量反馈的三组补充数据

本次只读核查了保留的 `verification.json`、对应 before/after prompt JSON、生成它们的核查脚本，以及当前 `logs/llm.log` 的相应输入行；未调用模型、未执行候选、未重跑回放，也未修改源码。

**证据层级。** 以下三组主要证明输入投影或修复对象选择发生了什么变化。原生成输入第 191、215、250、253、298、304、306 行的当前 SHA256，均与当时 verification 保存的值相同。对应早期 native report、stderr、compile output 等已被清理，本次无法从这些原文件重新算出当年的判断；当时的哈希不变结论只能作为保存的核查记录引用。before/after payload 仍在，本次直接复读并核对了其字段差异。所有“修复后”均指离线重建后的输入，除非另有明确标注；它们不是上线后的新模型回答。

## 1. 可执行错误已经发生，具体原因却没有到达生成器

**现场记录与数据。** conformance 的真实生成 INPUT 215 针对候选 `00e4dc3d61e0`，原始输入没有 `TypeError: ReplayHandler._write_trace() takes 5 positional arguments but 6 were given`。保存的核查记录从当时各组 stderr 取得该异常；本次确认原输入行指纹一致，保留的 before payload 不含异常，after payload 含异常。离线 A/B 保留候选、失败指纹、typed events、conformance 约束和源包，主要补入 `reason` 与 `error_type`。这将泛化的启动失败具体定位到 `_write_trace` 参数数量不匹配。

同类缺失在两条独立生成输入 250、253 中再次出现。保存的实际编译输出统计均为：**5 个 fixture 有 5 个不同 ID，但 5 个 service 只有 1 个不同 ID**。正式错误是 `duplicate replay service id: service-requirement-`，两条原输入却都缺少这条具体原因。保留的 A/B 在每条输入仅增加 204 个字符后，能够把下一步修复定位到 service ID；旧 fixture 约束没有重新激活，源包和既有 typed events 未改。

**字符数的解释。** conformance 重建 prompt 从 81,257 降至 71,848 字符，同时恢复关键异常。这并不说明增加信息天然会缩短提示：按核查时默认 JSON 序列化口径，active focus 增加 630 字符，重复 validation feedback 减少 10,039 字符，净减少 9,409。补入异常触发了已有 16,000 字符预算对重复字段的压缩，完整 active focus 保留原字段。紧凑 JSON 的分项变化为 +610/-9,651，不能混用两种口径。并且 conformance 重建省略了 frozen fixture shape summaries，并非完整历史 OptimizerRequest 的等字节重放。

**可用于正文的判断。** 更长的输入不保证更好的诊断。决定修复方向的是“哪个调用失败、因为什么失败”能否到达生成器；先前已修好的对象也需要从修复范围中排除。这里可直接量化的是诊断从缺失变为可见，以及 fixture/service 两类问题被区分。

**不能据此声称。** 这组 A/B 没有执行后续模型，不能声称模型因此一次修好、成功率提高或节约了多少真实回放成本。那是设计预期，不是本组测得的效果。

来源：
- [conformance 核查记录](/Users/wuman/Documents/workspace/aworld-self-evolve-conformance-stderr-20260918/tmp/conformance-stderr/verification.json)
- [conformance before](/Users/wuman/Documents/workspace/aworld-self-evolve-conformance-stderr-20260918/tmp/conformance-stderr/before-prompt.json) / [after](/Users/wuman/Documents/workspace/aworld-self-evolve-conformance-stderr-20260918/tmp/conformance-stderr/after-prompt.json)
- [compile 核查记录](/Users/wuman/Documents/workspace/aworld-self-evolve-compile-feedback-20260918/tmp/compile-feedback/verification.json)
- [INPUT250 before](/Users/wuman/Documents/workspace/aworld-self-evolve-compile-feedback-20260918/tmp/compile-feedback/250-before-prompt.json) / [after](/Users/wuman/Documents/workspace/aworld-self-evolve-compile-feedback-20260918/tmp/compile-feedback/250-after-prompt.json)

## 2. 测量分组没有丢失分数，却在转换中丢失了责任归属

**第一份保留的报告复盘。** `history-focus/offline-verification.json` 对 `f9767c106a83` 的重建显示，validation 记录只有 `evidence_quality` 失败，held-out 的失败列表为空；旧 focus 却选中 held-out，修复后转向 validation。其他 10 条反馈保持不变，反馈为 12,723/16,000 字符。关键变化不在多保留约 1,794 个字符，而在把正确分组的失败与候选源包绑定起来。旧焦点讨论 GPT-4 例子和摘要覆盖，新焦点对应 ROPD “完全规避”“从根源上规避”这类缺乏直接证据的强断言。

**第二份同周期复盘是独立的现场。** `intra-cycle-verification.json` 针对实际 INPUT 191 的父候选 `91556acf1380`，使用保存的 **48 份原始 judge 输出**，即 validation/held-out × baseline/candidate × 各 12 次评分输出。记录中 validation 候选有 **1/12** 次 `evidence_incomplete`，validation 基线为 **0/12**；held-out 候选和基线均为 **0/12**。相同重建输入下，旧 focus 为 held-out，新 focus 为 validation，另 11 条无关反馈保持不变。旧 focus 的 GPT-4/Claude 举例被替换为当前 validation 的问题，包括把 ROPD 称为对另一论文机制“immune”，以及未经支持的作者单位细节。

这两份复盘属于不同候选，不能把其分数、字符数和原始 judge 数量拼成同一次实验。

**可用于正文的判断。** “收到失败反馈”仍不足以形成可用的学习信号：系统需要同时保存失败属于哪个分组、哪个候选，以及另一分组已经通过的事实。否则问题记录与修复对象错位，搜索就可能继续修改不承担该次失败的部分。

**特别限制。** 同周期复盘从原生 judge 指标离线聚合，没有合并完整 runtime/replay 元数据；记录明确指出其离线 evidence gate 的 bundle/strategy 字段和某些派生约束不能冒充完整正式 gate。1/12 与 0/12 是保存的 judge 观测计数，不能称为正式 held-out 总体缺陷率，也不能当作 12 个独立任务。原 INPUT 191 本次仍可核对指纹；底层 9 份原 report/log/candidate 文件已不在，无法完整重演当年的聚合。

来源：
- [跨周期 focus 核查](/Users/wuman/Documents/workspace/aworld-self-evolve-history-focus-20260918/tmp/history-focus/offline-verification.json)
- [跨周期 before](/Users/wuman/Documents/workspace/aworld-self-evolve-history-focus-20260918/tmp/history-focus/before-prompt.json) / [after](/Users/wuman/Documents/workspace/aworld-self-evolve-history-focus-20260918/tmp/history-focus/after-prompt.json)
- [同周期核查及重建限制](/Users/wuman/Documents/workspace/aworld-self-evolve-history-focus-20260918/tmp/history-focus/intra-cycle-verification.json)
- [同周期 before](/Users/wuman/Documents/workspace/aworld-self-evolve-history-focus-20260918/tmp/history-focus/intra-cycle-before-prompt.json) / [after](/Users/wuman/Documents/workspace/aworld-self-evolve-history-focus-20260918/tmp/history-focus/intra-cycle-after-prompt.json)

## 3. 独立回归已经发现退化，生成器仍在修主评估的旧问题

**现场记录与数据。** 候选 `010507c00fbb` 的两组回归已有不同结论：标准套件基线 **89.83 → 候选 83.17**，判定 rejected；challenger **92.27 → 91.47**，判定 inconclusive。原始生成 INPUT 298、304、306 的 focus 都是 `historical_repair`，没有投影这两个套件；它们仍在引用论文任务中的 GPT-4/Claude 例子、样本效率等旧意见。本次核对三条当前日志行的指纹，均与保存记录一致。

**离线 A/B 改了什么。** 使用相同历史上下文，focus 中的独立回归套件数量从 **0 变为 2**，数据分组变为 `regression`。新的候选意见具体涉及工具指南中过强的 `type`、`back/forward/reload`、stale ref 和 full accessibility tree 例外等表述。每个套件保留自己的数据/执行身份、分数、决定和原始意见；每个角色有界保留 3 条意见，原门禁、评分与源包不变，运行支持文件保持冻结。三条重建反馈分别占 14,024、14,580、14,580 字符，均在原 16,000 预算内。同周期 A/B 也显示 `unattributed → regression`，说明同一遗漏横跨同周期构建和跨周期重建。

**可用于正文的判断。** 多一道回归 gate 只能帮助拒绝坏候选；要形成持续改进，它发现的问题还必须以自己的对象和来源到达生成器。否则验证成本已经发生，搜索器却继续按先前数据分布的错误修改。这里衡量的是“有 2 组真实测量，但模型焦点中为 0 组”的断裂，比单列提示增加了多少字符更能解释失败。

**可选补充：混合健康状态。** 后续 mixed-regression 的保留记录里，标准套件为 0/0、`judged=false`（没有可用 judge 信号），challenger 为 94.43/93.43、`judged=true`，且候选责任为可修复未决。旧全局不可修复状态将有效局部反馈一起压掉，焦点退回旧候选 `7c5067`；4 条实际输入上下文的离线 A/B 改为当前 `b5674d` 的 regression，同时保留全局 owner/repairable、两个套件原字段和 runtime 冻结。它说明“测量不可用”和“有测量但表现未决”需要分别传递；不能把 0/0 当成两边表现相同，也不能用健康套件覆盖不健康套件的验收失败。此条只宜作第三组的延伸，不与标准回归 A/B 合成一个因果实验。

**不能据此声称。** 从 0 组到 2 组证明了修复后输入能表达当时的回归结果，不证明模型理解后一定提高。v13 后续候选的实际质量与最终 v19 成功不是本次 A/B 的随机或受控结果，不应记为该投影修复带来的因果收益。

来源：
- [回归反馈核查](/Users/wuman/Documents/workspace/aworld-self-evolve-regression-feedback-20260918/tmp/regression-feedback/verification.json)
- [INPUT298 before](/Users/wuman/Documents/workspace/aworld-self-evolve-regression-feedback-20260918/tmp/regression-feedback/298-before-prompt.json) / [after](/Users/wuman/Documents/workspace/aworld-self-evolve-regression-feedback-20260918/tmp/regression-feedback/298-after-prompt.json)
- [同周期 before](/Users/wuman/Documents/workspace/aworld-self-evolve-regression-feedback-20260918/tmp/regression-feedback/intra-before-prompt.json) / [after](/Users/wuman/Documents/workspace/aworld-self-evolve-regression-feedback-20260918/tmp/regression-feedback/intra-after-prompt.json)
- [混合回归核查](/Users/wuman/Documents/workspace/aworld-self-evolve-mixed-regression-20260918/tmp/mixed-regression/verification.json)

## 三组数据共同支持的有限结论

保存测量结果、把结果投影到模型输入、由模型选择修改方向、执行修改后获得更高质量，是四个不同环节。前三组都定位到了第二个环节的具体断裂，并通过同输入离线 A/B 验证了信息恢复或对象选择变化。它们支持“先确认学习信号到达了正确对象，再讨论生成能力”的工程判断；它们没有独立识别这些框架修复对最终质量、成功率或成本的因果效果。
