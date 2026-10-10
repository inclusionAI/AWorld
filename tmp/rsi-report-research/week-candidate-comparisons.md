# 一周候选的多目标对照：v7—v19

2026-09-18 只读核查。原生 v18/v19 分数、门禁和父代身份另存 [week-candidate-comparisons.json](/Users/wuman/Documents/workspace/aworld/tmp/rsi-report-research/week-candidate-comparisons.json)，包含 source SHA-256。没有运行优化或测试。

**不能把下表候选分数连成能力曲线。** 每行只和该行的配对基线比较；不同轮 judge 抽样、部分回放基线及框架条件不同。v18 cycle003/005 分数还包含一次额外复评的全部样本，和普通三次 judge 的行不具有相同抽样量。“11/11”是回放任务完成数；主评分通常只有 4 个案例各评 3 次。

## 精简对照表

| 运行/候选 | 回放 | 同轮评分：基线→候选，Δ | 证据、延迟及结果 | 随后的真实变化/保留范围 |
|---|---:|---|---|---|
| v7 cycle002 / df358 | 11/11〔历史〕 | 主：89.2667→87.9333，−1.3334〔历史〕 | score 未决，未通过 | 后续候选仍未决；本次未找到可原生核对的下一候选变更，不推断 |
| v8 / 424d | 11/11〔历史〕 | 标准回归：95.6667→93.6667，−2.0000〔历史〕 | 主评通过，独立回归拒绝 | 当时修复反馈中的过时运行计划与 token 增长上限表达；不据此归因下一轮分数 |
| v13 cycle001 / 010507 | 11/11〔历史〕 | 标准回归：89.8333→83.1667，−6.6667；challenger：92.2667→91.4667，−0.8000〔保存的离线核验〕 | 主评全部通过；标准回归拒绝，challenger 未决；主候选分90.8167也没阻止回归失败〔保存 INPUT〕 | 后续框架将独立 suite 身份、分数、判定及有限原始 issue 传给修复器，避免继续投主评旧意见 |
| v15 cycle002 / 536 | 11/11〔历史〕 | 主：87.2833→89.4167，+2.1334〔历史〕 | score/成本延迟/held-out已过，只剩 validation evidence | 实际后续796仍从旧父代a370重起；修复`65bfe7351`才使 focus 选择536/validation，保留它已过的held-out检查点〔保存A/B〕 |
| v18 cycle001 / 7abd | 11/11〔原生〕 | 主：87.2500→87.1833，−0.0667 | 延迟−3.45%；validation证据过，held-out证据退化；score未决，拒绝 | 3f7、bfdf、134三者后来都从7abd分叉，支撑文件全部继承 |
| v18 cycle003 / 3f7 | 11/11〔原生〕 | 主：87.3083→88.0167，+0.7083；24个评分，CI [−1.6671,3.0838] | 分数走非劣通过，证据门禁过；**延迟+69.58%拒绝** | 随后的bfdf不是3f7子代；它再次取7abd为父代，改写另一种证据规则 |
| v18 cycle005 / bfdf | 11/11〔原生〕 | 主：86.5000→85.7750，−0.7250；24个评分 | 延迟+38.01%，在50%上限内；validation证据退化，score未决，拒绝 | 134也重新从7abd分叉，缩短抽取教程，增加“返回成功/元数据不能代替所需内容”的约束 |
| v18 cycle006 / 134 | 11/11〔原生〕 | 主：90.5000→89.0500，−1.4500 | 延迟−6.25%，证据门禁通过；仅score未决，campaign达到6轮预算后停止 | 之后集成历史窗口修复，再启动v19；不是在当前失败report上继续修改成绩 |
| v19 cycle001 / 6ac | 11/11〔原生〕 | 主：88.7333→88.1333，−0.6000 | 延迟+36.24%，门禁内；证据/held-out已过，仅score未决 | cb9b继承同一组三个support files，精简抽取教程并明确覆盖范围与未证实断言的限制 |
| v19 cycle002 / cb9b | 11/11〔原生〕 | 主：86.6667→89.4667，+2.8000 | 延迟−9.33%，token+1.43%；31门禁通过，隔离验收成功 | 没有额外统计复评；未发布源skill |

候选简称对应完整 ID：df358=`llm-mutator-df35853ef6db`；424d=`llm-mutator-424d89f2cfe9`；010507=`llm-mutator-010507c00fbb`；536=`llm-mutator-536d6353f1b3`；其余完整 ID 均在配套 JSON。

## 三条可增加正文深度的洞察

### 1. 搜索在多个要求之间移动；“这轮只剩一个失败”不是下一轮必然更好

v18 的现场很清楚：7abd 的延迟降低3.45%，却在 held-out 证据上退化；3f7 解决了当前证据门禁并通过分数非劣判定，却使平均回放延迟从132.747秒升至225.118秒；bfdf把延迟增幅降到38.01%的规则内，validation证据与分数又失败；134的延迟和证据门禁都过，主分却比同轮基线低1.45。所有这些候选都完成11/11回放，所以“能跑完”已无法区分它们。

本次另从3f7的11对成员 `metrics.json` 独立重算平均延迟，结果与门禁的+69.584586%一致。7/11案例变慢，其中 `task_20260521171856` 从128.875秒到591.362秒。这不是只凭某条“慢”的日志得出的印象。也不能写成某一句新规则必然导致69.58%变慢：没有消融，模型及执行仍有波动；这里只证明这个候选包的该次配对执行未满足延迟约束。

原生路径：

- `/Users/wuman/Documents/workspace/aworld/.aworld/self_evolve/campaign-f731a97f9fbde74e0968-cycle-003/report.json`
- 同目录 `replay/llm-mutator-3f7f80a2edba/members/manifest.json` 和每个成员的 `baseline/metrics.json`、`llm-mutator-3f7f80a2edba/metrics.json`。

报告用语建议：**改进不是只往一个分数方向爬，而是在质量、证据、执行代价和回归约束同时成立时才算完成。** 不能因134的89.05高于3f7的88.0167，就说它更强；前者同轮基线90.5，后者87.3083。

### 2. 看似四次进展，其实三个后续候选从同一个旧父代重新出发

原生 candidate JSON 的父代关系是：

```text
106 → 7abd ┬→ 3f7（缩短旧段落，加“删除无来源支持的名称、例子、断言”）
          ├→ bfdf（要求引述片段有canonical路径和可核字节范围）
          └→ 134（收短抽取教程，不能把传输成功或元数据当任务完成）
```

3f7、bfdf、134不是连续后代，全部 `parent_candidate_ids=[llm-mutator-7abd195e713f]`。本次逐字比较三个候选的 files 记录，它们的 capability/compiler/runtime 均与7abd相同；差异集中在SKILL文字。候选 rationale 对“极小修改、不会交易一个门禁换另一个”所作解释只是生成者意图，真正结果须看实际 gates，3f7的延迟失败正说明两者不相同。

这与历史窗口A/B相互印证：旧加载器的12条都来自cycle001；修复后才纳入cycle003/005已经通过的检查点。修复不是简单增加记忆长度，而是让固定12条名额能覆盖不同报告的有效进展。对RSI而言，应记录“实际继承了哪个经过验证的状态”，不能用cycle编号冒充代际继承。

源文件：v18各cycle目录中的 `candidates/llm-mutator-*.json`；历史窗口A/B：`/Users/wuman/Documents/workspace/aworld-self-evolve-history-window-20260918/tmp/history-window/verification.json`。本次此前已重核A/B所列18个源文件，SHA-256全部一致。

### 3. 有界复评确实会撤回看似正收益；独立回归也确实挡住主评分成功的候选

bfdf 的初次主评为85.7667→86.3500，差+0.5833，区间[−6.1921,7.3588]，未决。系统按规则追加一次，合并全部24个配对评分后，结果变为86.5000→85.7750，差−0.7250、区间[−4.7971,3.3471]，继续拒绝。数据来自cycle005原生 `score_improvement.details.initial_decision` 与最终同一gate。它是“不挑最好的一轮”的正面现场例子，也说明一次正均值不足以宣告收益。

3f7也用过一次复评：初轮差+0.2167、区间[−3.5173,3.9506]；合并后差+0.7083、区间[−1.6671,3.0838]，以既定非劣规则通过。通过仍不等于显著正提升；延迟门禁随后保留拒绝。**不要把主评分复评与v17后新补的独立回归复评混为一项：v18这两例是主评复评，v19最后两组独立回归未用额外复评。**

更早v13的010507主评过关，保存的INPUT中主候选分达90.8167；标准回归却从89.8333降到83.1667。该例原始report已清理，目前能核的是当时保存的INPUT与离线A/B记录，不应当作本次重新读取的原生报告。但它与v8的历史结果都支持一个工程判断：主任务局部变好，不足以替代保留任务上的复验。

## 证据质量及容易写错的地方

- v18/v19行来自现存原生report、candidate JSON；v7/v8及v15主配对分数只来自`.agent/progress.md`的历史记录，已在表中标明；v13回归数来自当时保存的离线核验文件。没有将历史摘要冒充本次原生核验。
- v13候选主分90.8167来自`/Users/wuman/Documents/workspace/aworld-self-evolve-regression-feedback-20260918/tmp/regression-feedback/298-before-prompt.json`中 `validation_feedback` 的 validation观察；缺少同层精确baseline，不补算主分差。
- v13两组回归分与失败归因来自同目录`verification.json`的 `cases[*].suites`；v15后续错误继承及通过检查点来自`/Users/wuman/Documents/workspace/aworld-self-evolve-frontier-order-20260918/tmp/frontier-order/verification.json`。不把修复后prompt重建叫做一次完整优化运行。
- v18成本gate中若 `cost_regression_ratio=null`，不填0、不说“token降低”。表中只给原生明确的latency比例；7abd虽有token减少11.78%的值，也不据此把其他候选的缺值补全。
- bfdf的最后一次单例原生judge仍指出，候选把推断写成论文结论，且说Paper 2 abstract截断，但索引中的unescaped artifact有完整abstract。这是“信息存在”与“被正确使用”不同的例子，不等于所有重复评审都一致认定该问题。路径：`/Users/wuman/Documents/workspace/aworld/.aworld/self_evolve/evaluator/campaign-f731a97f9fbde74e0968-cycle-005-candidate-8814c6f6c87c255b/llm-mutator-bfdf538f4287/validation/report.json`，case `task_20260522081711` 的 `judge.evidence_quality.evidence_issues`。
- 初始、复评合并与最后一次evaluator文件有不同口径；正式分数以原生run report的汇总gate为准。不要拿最后一次judge的report均值当全部样本均值。
