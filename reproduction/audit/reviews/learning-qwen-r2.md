PASS

# RSI-Flow 学习文档 scoped re-review（Qwen r2）

- 对象：`reproduction/LEARNING.md`（v0.1，基线 `9ea01ae8e922c2871b2630e85ff094020bd84330`）
- 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`
- 范围：仅核对首轮 Qwen I1/M1–M4 与 Kimi 两项 Minor 是否全部 ADDRESSED，并检查修订是否引入新事实错误；不重做全量审计。
- 输入：`learning-qwen-r1.md`、`learning-kimi-r1.md`、更新后的 `LEARNING.md` 与 `AGENTS.md`；回源 `persistent_meta.py`、`controller_tools.py`、`skill_memory.py`、`launch_meta.py`、`META_SKILLS.md`。
- 基线核对：属主身份 `setpriv --reuid=10045 --regid=60201 --clear-groups env HOME=/tmp git rev-parse HEAD` 实测 = `9ea01ae8e922c2871b2630e85ff094020bd84330`，与文档基线一致（进程级覆盖，未改 git 配置）。
- 性质：只读。未修改 LEARNING.md/AGENTS.md/生产代码/配置/账本；未启动实验/SCO/API；未读取凭据。仅写本报告。

## 结论

**PASS**：首轮 Qwen 1 Important + 4 Minor 与 Kimi 2 Minor **全部 ADDRESSED**；修订未引入新的事实错误；流程图固定层措辞已同步（`LEARNING.md:287`）。无新增 Critical/Important/Minor。

## 逐项 verdict

### Qwen I1 — "可执行控制机制固定" 绝对化 → **ADDRESSED**

- 修订证据：`LEARNING.md:46`「Meta 的实验方法论与验收协议固定…操作库不是封闭白名单，Meta 可以按协议修复或新增 operation，并直接使用原生 Codex 工具；这不等于允许 Meta 自主改写单组件选择、同批配对、严格正增益和事实沉淀等核心实验规则」；§6 第一层 `:256`「固定的是方法论与验收协议，不是操作实现的封闭集合；Meta 可以修复或新增 operation，但不能因此绕过核心实验规则」。
- 流程图同步：`:287`「方法与验收协议固定；操作实现可扩展；Astra 权重不训练」（已非旧版"工具机制固定"），符合维护约定 #4 图文同步。
- 回源：`persistent_meta.py:327`「You may fix or add implementations and use native Codex tools directly」、`:328`「Common operations (not a whitelist)」；`AGENTS.md:9` 已同步同口径。修订与代码一致，无新错误。

### Qwen M1 — 严格正增益/总分 → macro_success + activate_task 无守卫 → **ADDRESSED**

- 修订证据：`LEARNING.md:75`「成功率（默认指标 macro_success）具有严格正增益，Meta 才应激活候选…不是 activate_task 内部的自动正负守卫」；`:174`「compare_scores 报告父子成功率（默认 macro_success）」；`:176`「activate_task…不检查增益正负」；流程图 `:307/:346` 改为「成功率」。
- 回源：`persistent_meta.py:455`「strictly positive success-rate gain」；`controller_tools.py:242` `metric=arguments.get("metric","macro_success")`；`:313-321` `_activate_task` 仅按显式请求复制（`activated_as_requested`），无正负校验。修订准确，无新错误。

### Qwen M2 — 种子路径不精确 → **ADDRESSED**

- 修订证据：`LEARNING.md:228`「仓库提供可选的 `RSIFlow/meta_skills/reviewed_seed.jsonl`」。
- 回源：实际路径 `RSIFlow/meta_skills/reviewed_seed.jsonl` 存在，8 行 = 3 case/3 rule/2 incident（与文档计数一致）。无新错误。

### Qwen M3 — 候选组合未点名 compose_task → **ADDRESSED**

- 修订证据：`LEARNING.md:153`（HARNESS 步骤 7）与 `:163`（MODEL 步骤 4）均「通过 `compose_task` …组成候选 Task 状态」。
- 回源：`controller_tools.py:459` `_compose_task`；`persistent_meta.py:317` `RECOMMENDED_OPERATIONS` 含 `compose_task`。无新错误。

### Qwen M4 — "工具只执行请求" 宜补原生工具/可扩展交叉引用 → **ADDRESSED**

- 修订证据：`LEARNING.md:127`「工具只执行请求并返回回执…Meta 还可以直接使用原生 Codex 编码工具，并按需修复或新增 operation；这些扩展不改变控制器不选组件、不自动验收的职责边界」。
- 回源：`persistent_meta.py:327`；`controller_tools.py:238`「leave acceptance to Meta」。职责边界表述正确，无新错误。

### Kimi Minor 1 — procedure 版本/默认检索生命周期说明不足 → **ADDRESSED**

- 修订证据：`LEARNING.md:216` 新增生命周期段：`rule_update` 追加规则修订；procedure `revise` 创建带 `previous_version` 的新版本并标为未验证修订，旧支持证据仅留 `prior_version_support`，不能冒充新版本实测支持；`supplement` 只补支持/反证、不自动升级版本或置信度；`revise/merge/retire` 改变默认检索 active，历史可经 `include_inactive=true` 或 `read_skill` 查阅。
- 回源逐条命中：`skill_memory.py:360` `previous_version`、`:361` `version+1`、`:362` `confidence="unvalidated_revision"`、`:358` `prior_version_support`、`:353` 注释「Never reinterpret the original measured response as a new version's result」、`:385-387` supplement 仅设 support/counterevidence（不升版本）、`:204/:240` `target["active"]=False`、`:426/:434` 默认检索排除 inactive、`:66` `include_inactive=true`。修订准确，无新错误。

### Kimi Minor 2 — 初始化 CLI 参数名错误 → **ADDRESSED**

- 修订证据：`LEARNING.md:230`「实际 CLI 参数是 `--skill-seed`。`RSIFlow/META_SKILLS.md` 当前仍写 `--meta-skill-seed`，这是已知文档差异，应以 launch_meta.py 的 argparse 为准；该参数只初始化新账本，resume 不会重放种子」。
- 回源：`launch_meta.py:493` `parser.add_argument("--skill-seed", …"optional JSONL seed for a NEW Meta ledger; resume preserves existing skills")`、`:500-501` 映射到 `meta_skill_seed_path`、`:542-543` detach 转发 `--skill-seed`、`:243`「preserve it on resume」、`:260-262` `skills_initialization.json` mode `seeded/empty`；`META_SKILLS.md:100` 确实仍写 `--meta-skill-seed`；`AGENTS.md:218` 已登记该已知差异。修订准确，无新错误。
- 备注：`META_SKILLS.md:100` 源文档本身的旧参数名仍未改（属生产文档，不在本次允许修改范围）；LEARNING.md 已正确指引以 argparse 为准，足以避免读者误用。源文档修正建议留待获准修改 META_SKILLS.md 时执行。

## 修订是否引入新事实错误

逐条回源核对上述所有新增/修改表述（§1/§2/§4.1/§4.2/§4.3/§4.5/§5.1/§5.2/§6/流程图/§11 维护日志），均与代码一致；**未发现新的事实错误**。维护日志 `:441-444` 如实登记 Kimi/Qwen 两份审计与 Codex 因上游 429 中断未生成报告（不计为 PASS/失败），归属清晰。

## 剩余限制

- 本次为 scoped 静态只读回源，未运行离线测试/真实 rollout/SFT/正式一轮/最终评估，未启动实验/SCO/API，未读取凭据。
- 仅核对首轮六项 finding 及其修订，未重做全量审计；文档其余章节沿用首轮已核实结论。
- `META_SKILLS.md:100` 源文档旧 CLI 参数名未改（不在本次修改范围），LEARNING.md 已正确规避；建议后续获准时修正源文档。
- 行号基于 `9ea01ae8`；代码或文档再变更后须重核。

## 建议

- 六项 finding 全部 ADDRESSED，scoped re-review 通过，无需进一步修订即可作为持续学习入口。
- 可选后续：获准修改 `META_SKILLS.md` 时把 `:100` 的 `--meta-skill-seed` 改为 `--skill-seed`，消除源文档差异（非本次阻塞项）。
