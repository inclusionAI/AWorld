# 第一任务的早中期研究摘记

研究对象：[Investigate rejected self-evolve](codex://threads/01a07c5a-7124-7c10-b9d9-b41fc213b1e1)。用途：供 RSI 心得报告整合。只读取任务公开消息、工作记录、Git 历史和局部提交差异；没有修改源码、运行优化或执行测试。

证据边界：已读取任务最近 8 轮及其全部更早页面。更早页面很多 turn 只有工具记录或空 items，因此不能声称逐句恢复了全部对话。保存的可引用消息为 `thread-1-messages.json` 与 `thread-1-older-messages.json`，不包含 reasoning。下列主要事实由 `.agent/progress.md:749–846` 和实际提交差异相互支持。早期四个 campaign 的原始 `report.json` 目前未在 `.aworld/self_evolve` 中找到，所以历史分数、通过数及具体现场均应称为“工作记录记载”，不能说本次重新测得。测试数字也是历史记录，本次未重跑。

Git 历史有重放前后的重复提交：正文优先使用当前分支可见 ID，括号内列任务当时记录的 ID。两者内容由 `git show` 核验。

## 1. 加了续跑轮次，却没有给续跑分配相应预算

- 现象：原始任务从一次 `Status: rejected` 开始，日志显示 campaign 走完 4/4 cycle，仅使用 3/4 个权威候选，最终停在生成阶段。其后的工作记录明确指出隐式 campaign 续跑预算、未完成 paired replay 的恢复都有缺口。
- 机制：新增 repair continuation 属于一次真实变异轮次，隐式总 token ceiling 却仍按原轮次算；另有后来的候选已经留下有效未完成 checkpoint，但顶层报告选择更早的已评估候选作为 repair focus，使未完成工作被遮住。
- 修复：隐式预算按新增一轮补足，显式操作者预算保持不变；从实际 authoritative iteration 找出 checkpoint，并核对候选指纹及 pending cases 后续跑，避免重复占用候选名额。默认轮次、单轮预算和候选数也一并提高，仍有上限。
- 证据：`.agent/progress.md:753`；`90ef70b8c`（`7bd4ccf90`），`aworld/self_evolve/campaign.py`、`cli_orchestration.py`；新增 `test_runner_recovers_nonselected_authoritative_replay_checkpoint`。
- RSI 启示：持续运行要保存“还有哪一步没测完”的身份和账目。笼统地继续尝试，会把已经付费获得的实验进度扔掉。这里也有真实预算扩容，不应包装为完全没有增加资源就获得了改进。

## 2. 安全净化代理让合规 provider 在生成入口被拒绝

- 现象：合并 main 后，`campaign-587521eb03e0d21501f5` 尚未生成候选便被 fail-closed 拒绝。
- 机制：候选生成的 sanitizer 包裹了已登记的 OpenAI provider；provider registry 做 exact-type 检查，见到的是未知包装类型，无法确认原有能力。
- 修复：登记受限的透明代理，代理自身不获得能力，只能委托给已通过审核、类型精确匹配的 provider。
- 证据：`.agent/progress.md:761`；`75767867c`（`f6af8cdf9`）`fix(self-evolve): preserve provider lowering through sanitizer`；历史定向测试 47 项通过。
- RSI 启示：优化器运行在宿主架构里。宿主一次集成就可能改变候选生成条件；必须把“生成器根本没获得合法运行环境”与“生成结果不好”分开。

## 3. 候选单边超时被误判为框架故障

- 现象：baseline 筛选成功，candidate 超时，campaign 却暂停并要求 framework handoff。
- 机制：已有候选单边截止分支仅识别 `replay_member_phase_timeout`；真实 backend 返回兼容格式 `timeoutexpired`。漏判后，“未观察到候选干预”的兜底逻辑创建了 framework blocker。
- 修复：在归因前统一识别两种物理超时，输出 `candidate_screening_deadline_exceeded`，抑制错误 framework 事件；真正没有干预证据时，原来的框架检查仍保留。
- 证据：任务更早消息中有完整根因与修复说明；`.agent/progress.md:770`；`c2966a530`（`cffa55ae9`）`measurement_execution_admission.py` 的实际差异；历史完整 self-evolve 测试 2526 passed, 1 skipped。续跑候选通过 screening 并进入 11 例回放，是现场验证。
- RSI 启示：错误归因会把搜索引向错误对象。候选失败应该改变候选，测量失败应该重测，框架失败才应修框架。仅仅延长 timeout 无法修复这条错误路由。

## 4. held-out 只测候选，变成了绝对零缺陷要求

- 现象：工作记录记载，`llm-mutator-57dce205cb64` 通过成对 score-confidence bound；`llm-mutator-036a478d5ef1` 完成 11/11 回放且 validation、held-out judge verdict 都通过，仍被 evidence gate 拒绝。
- 机制：validation 有 baseline/candidate 成对比较；held-out 却只评价 candidate，所以基线已有的证据问题也成了候选的绝对否决项。
- 修复：held-out 新跑同一 split 的 baseline 与 candidate，两边都合并 replay evidence、检查 runtime health，并只拒绝新增或恶化的证据约束；同步为两个 arm 预留 judge 预算。
- 证据：`.agent/progress.md:777`、`:783`；`b3760f2f7`（`f2382dc4c`）将 `evaluate_variant` 改为 `evaluate_pair`，并把 baseline 传给 evidence gate；admission 的 held-out 预算从 `+1` 改为 `+2`。
- RSI 启示：改进是比较命题。若两边的实验条件或判据不同，所谓严格验收可能只是不可达门槛。不过这是评估协议修正，必须如实记载，不能将修正后的通过解释为同一评估器下纯粹由候选变强带来的提升。

## 5. 修复包保留了内容，却丢了修改授权

- 现象：首轮候选通过结构门禁，后续只修 runtime source 时，同样的 SKILL 内容却被当作未授权删除 fenced block，多个 cycle 卡在物化。
- 机制：首轮 `patch_intent` 对章节替换有内容指纹绑定的授权。repair feedback 序列化只保留 `content/files`，丢弃 `structural_edit_intent`；子候选继承了修改结果，却没有继承其合法性证明。
- 修复：feedback、历史持久化、恢复和 mutator 都携带 typed structural edit intent；source-only repair 继承原授权，同时仍限制 producer path。
- 证据：任务更早消息明确将初步“无效父候选”假说纠正为授权元数据丢失；`.agent/progress.md:788`；`36cee0bb4`（`1c3135fad`）在 `run_iteration_helpers.py`、`feedback.py`、`feedback_history.py` 增加授权字段；`structure_types.py` 新增 typed loader。
- RSI 启示：跨轮记忆不能只是文本摘要。候选身份、产生路径、修改授权与内容一起构成可继承状态。只传结果，下一代可能无法合法继续工作。

## 6. 合法 runtime 写法超出了静态证明器的能力

- 现象：尚未形成权威候选就耗尽 repair frontier，结论为 `evaluation_support_composition_stalled`。
- 机制：runtime 从 response index 经 records、record 取 value 的逻辑本身正确，但数据流跨了分支赋值和后面的循环；有界 AST proof 无法证明最后的直接投影，每轮都被迫修 evaluation-support bootstrap。
- 修复：对 legacy list 与 canonical object 分别在分支内直接投影 `record['value']`，写成现有证明器能完整覆盖的结构。
- 证据：`.agent/progress.md:795`；`3eb5362fe` `fix(agent-browser): prove replay sidecar projection` 实际差异；历史四项 operation proof、198 项相关测试及 2531 项 self-evolve 测试通过。
- RSI 启示：一个候选能否进入实验，还受验证器表达能力限制。需要明确区分“被证明错误”和“当前证明器证明不了”，并把适合验证的实现范式提供给生成器。

## 7. 无状态 judge 被放进有状态 Agent runner

- 现象：11/11 可比回放与前两次 validation judge 都完成，后面的 held-out 评估却发出 `messages: []`，被 runtime health 拦截。
- 机制：markdown instructions judge 的每次独立提示都走 stateful Agent runner。嵌套任务在不同 async context 读写 task-local memory，后续 judge 丢掉了请求内容。
- 修复：默认 judge executor 直接调用配置的 LLM，每次初始请求、artifact read、schema repair 都构造新的 system/user pair；增加两次连续调用的隔离回归。
- 证据：`.agent/progress.md:802`；`5ab076cae`，`aworld/evaluations/substrate.py` 从 `exec_agent(..., Context())` 改为 `acall_llm_model(messages=[system,user])`。
- RSI 启示：评估器也会失效，而且越到后期才越容易暴露。11/11 replay 只证明执行层跑通，不能证明 judge 有效，更不能证明候选通过。

## 8. 局部 1/1 被当作全局额度已耗尽

- 现象：`campaign-8d67ebbd5f359e60731b` 已完成 paired validation 和 held-out judging，在 campaign 候选计数 11/12 时提前停止。
- 机制：derive disposition 先读到当前 run 的 1/1 authoritative slice，还没有拿到 campaign 累计 11/12，最后一个修复槽被误判为不存在。
- 修复：先投影本次结束后的累计权威候选数、有效轮数及上限，再决定 disposition；已计费的 pending candidate 不重复入账。
- 证据：`.agent/progress.md:809`；`4e3feb04b`（`6237918ac`）`campaign.py` 中将 cumulative projection 前移到 derive disposition 前。
- RSI 启示：分层调度不能混用统计口径。生成次数、筛选次数、权威候选、测量重试和 campaign 总额必须有各自身份，否则既可能超支，也可能在仍有预算时过早终止。

## 9. 长期运行暴露了服务生命周期成本

- 现象：`campaign-9aa6215c1242714129ed` 已有 11/11 checkpoint，后续 preflight 却反复在首个 framework HTTP fixture 绑定 loopback port 前超时；outer supervisor 活着，child 没有输出。
- 机制：工作记录记载同一冻结 capability 在宿主重启后立即通过，因此将原因隔离到积累的进程压力，而非候选行为。此前还有终结清理同步遍历历史 replay workspace 的开销。
- 修复：不能 fork 的 framework fixture 直接在受限进程组运行，skill runtime 继续用 descendant supervisor；随后补充 framework fixture 对 optimize parent PID 的进程内监控。清理工作另做五秒上限及可恢复 quarantine transaction。
- 证据：`.agent/progress.md:765`、`:817`、`:824`、`:832`；`906bd3181`、`732497c4f`、`852fe62e3`。历史验证记录为连续 12 次五服务 preflight 无残留进程。
- RSI 启示：小时级闭环的资源释放、取消和恢复是实验有效性的一部分。短跑测试通过，不能证明长跑没有状态积累。这里“进程压力”是现场对照支持的诊断，未留存本次可复读的原始进程快照。

## 10. 回放对任意路径都返回同一内容，诱发无效检索

- 现象：一名 repair candidate 修复了先前 evidence-production 失败并完成 10/11 candidate arms，最后在第 33 次工具调用触碰 32 次上限，没能输出总结；文字层面的修复继续增加调用和 token。
- 机制：browser replay runtime 不区分 URL 路径，尝试 `/html`、`/pdf`、favicon 都返回 HTTP 200 与同一 payload，错误探索得不到“没有更多来源”的反馈。另有 prompt 接受 `bounded_fields`、validator 却拒绝别名，以及续跑后写死旧工作区路径的接口不一致。
- 修复：绑定框架选中的 exact response record 和原始 task URL path；未记录路径确定返回 404。为 manifest 提供精确 JSON 形状，规范化字段别名但不扩张外部路径权限；prompt 使用环境变量定位当前工件目录。筛选/权威回放采用分层 12/48 次工具上限，给真实第 33 次调用留下综合答案的余量。
- 证据：`.agent/progress.md:824`、`:832`、`:836`、`:839`；`852fe62e3`（`b4d99d509`）的 `runtime.py`、`replay.py`、`measurement_execution.py`、`screening_helpers.py`；历史末次相关测试 191/191、完整 self-evolve 2540 passed, 1 skipped。
- RSI 启示：让 agent 更努力地查证，可能只是在不真实的环境中循环。应先让环境准确表达“有、无、未知”，再调整候选策略。提高工具额度确实参与了修复，不应只把成功归于 prompt。

## 与后期报告衔接的三个观察

1. 第一任务后段多次出现“框架基本稳定”的判断，随后又发现 dedup、fenced block、judged-repair 膨胀等问题。因此可写成“故障逐渐从运行条件转向评估和搜索策略”，不宜写成某一天起框架已被全面证明稳定。
2. 第一任务最新消息记载：一名候选 score 86.3→88.7、成本/延迟通过，仅 evidence 失败；修复却丢失已通过条件。另一名候选通过主评估，在独立 challenger 上 baseline 94.53、candidate 91.27，差 -3.27。这两组数字来自任务叙述，需由负责后期材料的研究者核对原报告再采用。
3. `c5c411096`/`bc22a937d` 与 `58bfda10d`/`bf83db6b6` 体现后期路线：保留已有进展、退出被新鲜复评否定的旧候选、让证据修复优先删减无依据陈述，独立回归修复必须收缩和限域。曾把两段改进扩成 46 行规则，或将 38 行扩成 58 行。这提供一个很具体的经验：增加规则容易，保住已验证收益更难。

## 必须保留的事实修正

任务更早消息曾将某次“canonical evidence 库存为空”归因于框架没有物化受信工具结果，提出生成内容寻址收据。后来的 `.agent/progress.md:781` 明确记载，该 campaign 最后一个终结失败成员只写了 advisory manifest，指向当前 evidence namespace 之外，其失败正确属于 candidate-owned。两者未必是完全相同现场，本次没有原始 report 可进一步判定。因此不要把那条早期口头诊断作为已经落地并证实的框架缺陷；可以用它说明归因会被新证据修正。

## 可直接用于心得正文的判断

这段探索最有价值的收获，不是找到了一个更长、更严的提示词，而是逐步明确了“什么才算一次有效改进”。候选要在真实且一致的环境里执行，评估两边要对等，失败信息必须归到正确对象，下一轮还要继承上一轮已经验证过的内容与约束。缺少其中任何一环，自动迭代可能只是重复花费预算，甚至把已经接近通过的候选越修越差。

它也说明这个案例同时优化了被测 skill 与 self-evolve 框架。本案例不能只用一次最终 success 来证明通用 RSI 已成立；更有说服力的证据应是，在固定评估协议、不同任务分布和重复测量中，仍能保留收益，并知道何时证据不足而停止。
