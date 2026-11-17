CHANGES

# RSI-Flow 学习文档独立审计（Kimi r1）

结论：`reproduction/LEARNING.md` 的核心机制、协议边界和当前 v5 配置描述准确，未发现 Critical 或 Important。两项 Minor 涉及版本维护说明的完整性及初始化交叉文档的命令准确性；修订后更适合作为持续维护的学习入口。

## 已回源核对的范围

- 文档基线 `9ea01ae8e922c2871b2630e85ff094020bd84330` 与当前工作树 HEAD 一致（`reproduction/LEARNING.md:3-6`）。
- Task/Meta 组成：`reproduction/LEARNING.md:32-46,85-99` 与 `reproduction/configs/base_4b.json:4,55-65`、`AGENTS.md:7-9` 一致。当前 Meta 模型/provider 来自有效 base 配置，Task checkpoint 是 Qwen3-4B。
- Meta 通过工具构造候选：`reproduction/LEARNING.md:101-177` 与 `RSIFlow/persistent_meta.py:311-343,346-421,424-478`、`RSIFlow/controller_tools.py:74-127,129-158` 一致。原生 Codex 编辑与 experiment helper 并存，controller 只分发请求和返回事实。
- 完整同批配对、严格正增益和拒绝后同轮 attempt：`reproduction/LEARNING.md:63-75,172-177,334-398` 与 `RSIFlow/persistent_meta.py:450-456` 一致。`compare_scores`/`compare_task_differences` 只报告事实，`activate_task` 只执行 Meta 显式选择（`RSIFlow/controller_tools.py:237-321`）；文档没有把协议要求误写成 controller 自动验收。
- 技能账本类型与追加语义：`reproduction/LEARNING.md:187-214` 所列 `case/rule/procedure/incident/rule_update/skill_maintenance` 与 `RSIFlow/skill_memory.py:113-121,166-249,267-401`、`RSIFlow/META_SKILLS.md:8-32` 一致。案例 ID、原则 ID、证据边界和“引用不等于理解/关联不等于因果”均有正确说明。
- 默认 empty 与可选 seed：`reproduction/LEARNING.md:216-226` 与 `reproduction/configs/train_4b_v5.json:3-6` 一致。`RSIFlow/launch_meta.py:254-264,493-505` 证明只有新账本按显式 seed 初始化，resume 不重放；`skills_initialization.json` 区分 `empty/seeded`。`RSIFlow/meta_skills/reviewed_seed.jsonl:1-8` 确为三个 cases、三条 rules、两个 incidents，当前配置未启用。
- 最终评估只报告：`reproduction/LEARNING.md:383-390,412-419` 与 `RSIFlow/skill_memory.py:71-73`、`RSIFlow/META_SKILLS.md:86-89` 一致。`reproduction/configs/train_4b_v5.json:90-93,127-163` 支持当前 `skip_acebench=true`、694/744 计划和 `final_only` 边界；文档没有宣称最终评估已经完成。
- Preflight 与正式一轮：`reproduction/LEARNING.md:274-276,400-410` 明确区分机制 smoke 与正式 B1–B5 闭环；固定 S2 训练不被写成 Astra 的正式 MODEL 决策，符合 `AGENTS.md:219` 的证据分级要求。
- 可维护性：文档已有适用范围、事实/配置/观察/假设区分、同步修改要求、推荐回源顺序和维护日志（`reproduction/LEARNING.md:8-26,421-438`）。

## Critical

无。

## Important

无。

## Minor

### 1. procedure 的版本与默认检索生命周期说明不足

- 文档位置：`reproduction/LEARNING.md:187-214,228-248`。当前只说明 procedure 有版本、旧案例不覆盖和追加式修订，没有解释 revise/merge/retire 对新旧版本及默认检索的不同影响。
- 代码证据：`RSIFlow/skill_memory.py:350-376` 显示 revise 创建 linked record，设置 `previous_version`、递增 `version`，并把新修订标为 `hypothesis/unvalidated_revision`；旧支持证据进入 `prior_version_support`，不能冒充新版本的实测支持。`:185-249` 显示 revise/merge/retire 会停用旧记录或合并证据；`:425-435,507-517` 显示默认检索排除 inactive，而历史记录仍可经 `include_inactive=true` 或 `read_skill` 读取。对应权威说明见 `RSIFlow/META_SKILLS.md:34-55`。
- 影响：核心陈述没有错误，但维护者可能误以为“追加修订”只是给同一 procedure 累加证据，或把旧版本支持直接继承为新版本置信度。
- 建议修订：在 5.1 后增加一段简短生命周期说明：rule 通过 `rule_update` 追加修订；procedure 的 revise 创建新版本并重置为未验证状态；supplement 不自动升级版本/置信度；revise/merge/retire 保留历史但改变默认检索可见性。

### 2. 推荐的初始化阅读链路仍含错误 CLI 参数名

- 文档位置：`reproduction/LEARNING.md:226,428-430` 将 `META_SKILLS.md` 作为案例、规则和初始化协议的推荐阅读；`LEARNING.md` 自身只写配置键 `meta_skill_seed_path=null`，这一事实是正确的。
- 代码证据：`RSIFlow/META_SKILLS.md:100-101` 仍建议使用 `--meta-skill-seed`，但实际 argparse 和 detach 转发均为 `--skill-seed`（`RSIFlow/launch_meta.py:493,500-505,540-543`）。`AGENTS.md:217` 也已记录该已知差异。
- 影响：读者按推荐资料显式启用 reviewed seed 时会得到未知参数错误，削弱学习文档作为维护入口的可靠性；不影响当前 v5 默认 empty 结论。
- 建议修订：后续获准修改文档时，将 `META_SKILLS.md:100` 改为 `--skill-seed`；在源文档修正前，`LEARNING.md` 的初始化段可直接列出实际参数名，避免读者沿用旧命令。

## 限制

本次仅做静态只读回源核对，没有运行离线测试、真实 rollout/SFT、正式一轮或最终评估，也未启动实验、SCO 或 API，未读取凭据。报告不证明模型能力提升、技能学习收益或正式实验完成；这些仍须以独立真实证据验收。
