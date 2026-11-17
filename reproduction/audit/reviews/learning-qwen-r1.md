CHANGES

# RSI-Flow 学习文档独立审计（Qwen r1）

- 审计对象：`reproduction/LEARNING.md`（v0.1，2026-11-17，自称基线 `9ea01ae8e922c2871b2630e85ff094020bd84330`）
- 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`
- 回源文件：`RSIFlow/persistent_meta.py`、`RSIFlow/controller_tools.py`、`RSIFlow/skill_memory.py`、`RSIFlow/META_SKILLS.md`、`RSIFlow/task_adapter.py`、`RSIFlow/runtime/rsiflow/task_meta/evolution_protocol.py`、`reproduction/configs/train_4b_v5.json`、`reproduction/configs/validation_744_v5.json`、`RSIFlow/meta_skills/reviewed_seed.jsonl`、`AGENTS.md`
- 基线核对：以属主身份 `setpriv --reuid=10045 --regid=60201 --clear-groups env HOME=/tmp git rev-parse HEAD` 实测 = `9ea01ae8e922c2871b2630e85ff094020bd84330`，与文档自称基线**一致**（进程级覆盖，未改任何 git 配置）。
- 性质：只读审计。未修改生产代码、`LEARNING.md`、配置、账本；未启动实验/SCO/API；未读取凭据。仅写本报告。

## 结论

**CHANGES**：0 Critical，1 Important，4 Minor。文档对核心机制的描述与代码**高度一致**，所有被抽查的关键合同均回源命中；问题集中在少量**绝对化/易误导表述**与路径/操作名精确度，无颠覆性事实错误。建议按下列 Minor/Important 修订后即可作为持续学习文档使用。

## 一、已回源核实为正确的关键 claim（核对范围）

| 文档 claim | 代码证据 | 结论 |
| --- | --- | --- |
| Task = Qwen checkpoint + Harness + Artifacts；Meta = gpt-6-astra + 持久 Codex 线程 + 工具 + 技能记忆（§1/§3） | `AGENTS.md` §1/§3；`persistent_meta.py` `NativeExperimentTools`/`dynamic_tools`；`train_4b_v5.json` meta provider | ✓ |
| 候选由 Meta 通过工具构造，非 Qwen 自主决策；工具不选组件、不自动接受（§2/§4.1/§4.5） | `controller_tools.py:238` "leave acceptance to Meta"；`:313-321` `_activate_task` "selected *by Meta*" / `activated_as_requested` | ✓ |
| 四个动态工具 experiment/job_status/wait_job/finish_experiment；experiment 含 operation+命名参数；长作业返回 job_id（§4.1） | `persistent_meta.py:324-340` 四工具定义；`:320` `LONG_OPERATIONS={run_parent,run_candidate,rollout,sft,evaluate,run_command}`；`:378` 长作业走 `jobs.start` | ✓ |
| finish_experiment 缺项返回 Meta（§4.1） | `persistent_meta.py:336-339` "Missing products are returned to you" | ✓ |
| HARNESS：materialize→模块定位报告→改进方向报告→完整候选→rsiflow 验证（最多三次）→run_candidate（§4.2） | `controller_tools.py:129` `_materialize_harness`；`task_adapter.py:343` `checks_remaining=3 - attempt`；`RECOMMENDED_OPERATIONS` 含 materialize_harness/rsiflow/run_candidate | ✓ |
| MODEL：父代合格成功轨迹 positive-only LoRA SFT→merge→同批评估（§4.3） | `task_adapter.py` sft 阶段；`persistent_meta.py:455` 配对/正增益；`META_SKILLS.md:82` "strictly positive first-candidate gains" | ✓ |
| ARTIFACTS：direct_submission 直接重评分（§4.4） | `task_adapter.py:69/217/405` `artifact_evaluation="direct_submission"` | ✓ |
| compare_scores 报告增量/身份/轨迹配对；compare_task_differences；activate_task 仅显式请求（§4.5） | `controller_tools.py:237-258` `_compare_scores`（delta/matching_facts/trajectory_pairing）；`:402` `_compare_task_differences`；`:313` `_activate_task` | ✓ |
| 技能账本 meta/skills.jsonl 追加式；索引可重建；记录类型 case/rule/procedure/incident/rule_update/skill_maintenance（§5/§5.1） | `META_SKILLS.md:3`；`skill_memory.py:115-121` 类型归一、`:170/186` rule_update/skill_maintenance、`:1` "Append-only" | ✓ |
| skill.<COMPONENT>.<id> 案例；principle.<id> 仅有证据时；rule_update strengthen/weaken/contradict/supersede；maintenance supplement/revise/merge/retire（§5.1） | `skill_memory.py:45/49`；`:235-236` {weaken,strengthen,contradict,supersede}；`:53-60` add/supplement/revise/merge/retire | ✓ |
| 旧案例事实不被覆盖；调用次数不自动提高置信；引用≠理解、关联≠因果（§5.1） | `skill_memory.py:62-66` "cases remain immutable… does not automatically strengthen confidence"；`:232` "case facts retained" | ✓ |
| 默认 empty（meta_skill_seed_path=null）；可选 reviewed_seed 含 3 case/3 rule/2 incident（§5.2） | `train_4b_v5.json:5` `meta_skill_seed_path: null`；`RSIFlow/meta_skills/reviewed_seed.jsonl` 实测 8 行 = {case:3, rule:3, incident:2} | ✓ |
| 检索有字符/类别预算；空记忆/无匹配合法（§5.3） | `train_4b_v5.json:3-4` `meta_skill_context_chars=24000`、`meta_skill_per_category=1`；`skill_memory.py:4` "bounded retrieval… never chooses a Task component" | ✓ |
| 固定方法论 vs 动态 Meta 经验 vs 动态 Task 状态三层；经验增长≠能力提高（§6） | `AGENTS.md` §1 skill-based variant；`persistent_meta.py:455-463` 协议提示 | ✓（措辞见 Important 1） |
| 最多 48 条代表轨迹，每领域 16，8 成功/8 失败（§7） | `evolution_protocol.py:249-258` `meta_routing_rows(maximum=48, per_domain=16, per_outcome=8)`，"3x16 with 8 success and 8 failure"；`task_adapter.py:165` | ✓ |
| 拒绝保留父代、同轮同批新 attempt、复用未变父代结果、保留每个 attempt、每次 attempt 含拒绝都 snapshot（§2/§7） | `persistent_meta.py:450-453` "Rejection retains the parent… same round and task batch… reuse unchanged parent results and preserve every earlier attempt"；`:463` snapshot after each attempt incl rejected | ✓ |
| 最终评估不反哺训练/接受/Meta；694/744、ACE 50 pending；每轮 600×5=3000（§7/§9） | `validation_744_v5.json:11` `feedback_to_training_or_meta: false`；`train_4b_v5.json:92-93/153` run_name eval694_v5、skip_acebench=true、data_release v5 | ✓ |
| preflight 不是正式一轮；训练侧 smoke 不调用 Meta/不跑 pipeline.run（§8） | `AGENTS.md` §3/§8；training_side_smoke 设计（不调用 pipeline.run/Meta） | ✓ |
| 代码基线 commit（头部） | 属主身份 git rev-parse HEAD 实测一致 | ✓ |

## 二、Important

### I1 — "可执行控制机制固定" 绝对化，与 experiment 工具允许 Meta 修复/新增实现相矛盾

- 位置：`reproduction/LEARNING.md:46`「当前发布是 skill-based variant：Meta 的实验方法和**可执行控制机制固定**，跨 attempt、跨轮变化的是经验内容和 Task 状态。」
- 代码证据：`RSIFlow/persistent_meta.py:327` experiment 工具描述明确「**You may fix or add implementations and use native Codex tools directly.**」；`:328`「Common operations (**not a whitelist**)」；`RECOMMENDED_OPERATIONS`（`:311-319`）仅为推荐集合，`_request`（`:362-381`）对新增 operation 直接走 freshly loaded tool library。
- 问题：文档把「可执行控制机制」整体说成固定，会让读者误以为 Meta 不能改动/扩展任何可执行操作代码。实际上**固定的是实验方法论与验收协议**（单组件、同批配对、严格正增益、事实记录、技能维护规范），而**操作库（operation handlers）允许 Meta 修复/新增**，且可直接使用原生 Codex 编码工具。这与 `AGENTS.md` §1「Meta 的可执行程序固定」措辞同源，但相对代码不够精确。
- 建议修订：将 :46 改为「Meta 的**实验方法论与验收协议**固定（单组件选择、同批配对、严格正增益、事实沉淀、技能维护）；操作库不是白名单，Meta 可按需修复/新增 operation 实现并直接使用原生 Codex 工具，但核心实验循环与验收规则不由 Meta 自主改写。跨 attempt/跨轮积累的是经验内容与 Task 状态。」并在 §6 第一层（:254-256）补一句区分「方法论固定」与「操作实现可扩展」。

## 三、Minor

### M1 — "严格正增益/总分" 未点明是 success-rate（macro_success），且未说明该规则由协议/Meta 执行而非 activate_task 代码强制

- 位置：`reproduction/LEARNING.md:75`「只有完整配对结果满足严格正增益，才允许激活候选」；`:174`「compare_scores 报告父子总分、增量」；流程图 `:307/:346/:353`「总分」「严格正增益」。
- 代码证据：`persistent_meta.py:455`「Use complete same-batch paired scores and **strictly positive success-rate gain** to decide activation」；`controller_tools.py:242` `metric = arguments.get("metric", "macro_success")`（默认成功率，非原始总分）；`controller_tools.py:313-321` `_activate_task` 仅按 Meta 请求复制（`activated_as_requested`），**不校验增益正负**。
- 问题：「总分」易被理解为原始分数总和；验收判据实为**成功率（macro_success）严格正增益**。「才允许激活」是协议/Meta 决策规则（prompt 强制），而非 activate_task 的代码守卫；§4.5（:176）虽已说工具不替 Meta 验收，但 :75 与流程图仍可能让读者以为工具会拦截非正增益激活。
- 建议修订：把「总分」统一为「成功率（macro_success）总分/增量」；在 :75 与 §4.5 注明「严格正增益是 Meta 必须遵守的验收协议（见 persistent_meta 主循环提示），activate_task 本身只按显式请求复制状态、不做正负守卫」。

### M2 — 可选种子路径不精确

- 位置：`reproduction/LEARNING.md:226`「仓库提供可选的 `meta_skills/reviewed_seed.jsonl`」。
- 代码证据：实际路径为 `RSIFlow/meta_skills/reviewed_seed.jsonl`（实测存在，8 行 = 3 case/3 rule/2 incident，与文档计数一致）。
- 建议修订：写全 `RSIFlow/meta_skills/reviewed_seed.jsonl`，避免与 run 目录下的 `meta/skills.jsonl` 混淆。计数与「当前配置未启用」均正确，无需改。

### M3 — 候选 Task 组合未点名 `compose_task` 操作

- 位置：`reproduction/LEARNING.md:153`（HARNESS 步骤 7）、`:163`（MODEL 步骤 4）「与父代…组成候选 Task 状态」。
- 代码证据：`controller_tools.py:459` `_compose_task`；`RECOMMENDED_OPERATIONS` 含 `compose_task`（`persistent_meta.py:317`）。
- 建议修订：在描述组合候选状态处点名 `compose_task` 操作，便于回源与工具调用追踪（非错误，仅精确度）。

### M4 — "工具只执行请求并返回回执" 宜补充原生工具/可扩展操作的交叉引用

- 位置：`reproduction/LEARNING.md:127`「工具只执行请求并返回回执，不负责选择改进组件，也不自动接受候选。」
- 代码证据：`persistent_meta.py:327` 允许 Meta 直接使用原生 Codex 工具并修复/新增实现。
- 问题：该句对 controller 职责的描述正确，但孤立阅读可能让读者以为 Meta 只能使用列出的 operation。与 I1 同源。
- 建议修订：在 :127 后补「此外 Meta 可直接使用原生 Codex 编码工具，并按需修复/新增 operation（见 §4.1 与 experiment 工具说明）；这些都不改变『控制器不选组件、不自动验收』的职责边界。」

## 四、适合持续学习的评估

- 结构良好：问答式 + 三层模型 + 完整 B_r 流程图 + preflight/正式边界 + 数据/证据边界 + 阅读顺序 + 维护日志，适合按主题持续补充。
- 维护约定（§文档目的 1–6）已要求回源核对、区分代码事实/配置/概念/观察/假设、图文同步、独立审计，符合持续学习治理。
- 已具备多处防绝对化护栏（:59 经验增多不保证单调提升、:272 能力提高需受控比较、§9 离线测试/completed≠能力增益），方向正确。
- 剩余改进空间：上述 I1/M1 的绝对化措辞是主要风险点；建议补充「操作库可扩展 vs 方法论固定」「成功率判据 vs 工具不守卫」两处区分，并在维护日志登记本次审计。

## 五、剩余限制（审计边界）

- 本审计为静态回源核对，**未运行**任何实验/SCO/API/模型，未验证运行时行为（如 wait_job 实际等待、长作业回执、技能检索真实命中）。
- 未逐行通读 `persistent_meta.py`/`controller_tools.py`/`skill_memory.py` 全文，仅核对文档 claim 涉及的函数与常量；未覆盖文档未提及的操作（如 `prepare_validation_snapshot`、`skill_usage_report`、`rebuild_skills_index`）的完整语义。
- `meta_routing_rows` 的 8 成功/8 失败为配额上限语义（`per_outcome=8`，按 error 分组轮询选取），文档「尽可能 8 成功/8 失败」与 AGENTS.md 一致；实际某类不足时按可用数选取，未在本审计中运行验证。
- 基线 commit 已实测一致；但文档若后续修订，需重新核对 HEAD 与代码行号（本报告行号基于 `9ea01ae8`）。
- 未读取任何凭据；git 仅以属主身份进程级执行 rev-parse，未改 trust/全局配置。

## 六、建议修订清单（供主 agent 回源复核后执行）

1. I1：`LEARNING.md:46` 与 §6 第一层区分「方法论/验收协议固定」与「操作库可扩展（非白名单）」，引 `persistent_meta.py:327-328`。
2. M1：`LEARNING.md:75/174/307/346/353` 将「总分」明确为成功率（macro_success），并注明严格正增益为协议/Meta 规则、activate_task 不做正负守卫（`controller_tools.py:242/313-321`、`persistent_meta.py:455`）。
3. M2：`LEARNING.md:226` 种子路径写全 `RSIFlow/meta_skills/reviewed_seed.jsonl`。
4. M3：`LEARNING.md:153/163` 点名 `compose_task`（`controller_tools.py:459`）。
5. M4：`LEARNING.md:127` 补原生工具/可扩展操作交叉引用。
6. 在 §11 维护日志登记本次独立审计（Qwen r1，CHANGES，0 Critical/1 Important/4 Minor）与主 agent 复核结论。
