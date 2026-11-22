# RSI-Flow 学习笔记：Task / Meta 双层改进闭环

- 版本：v0.5
- 整理日期：2026-11-22
- 代码基线：`9dd6b1dd68ed2e0bd190c491b256313a1721a641`（`reproduction/4b-main8`；本次学习文档更新不改生产代码）
- 适用范围：当前仓库的 4B、skill-based variant；不是对未发布变体或未来实现的泛化描述。
- 系统学习入口：`reproduction/STUDY_GUIDE.md`；本文继续承担 Task/Meta 机制连续问答。

## 文档目的与维护约定

本文从“Task agent 产生轨迹后，Meta agent 如何改进它”这一问题开始，整理连续学习问答，重点解释：

- Task agent 和 Meta agent 分别由什么组成；
- Meta 如何通过工具构造候选；
- 父子结果如何比较并决定接受或保留；
- 成功与失败经验如何进入 Meta 技能记忆；
- 固定的元改进方法论与动态经验之间有什么区别；
- 正式一轮演化的完整流程。

维护要求：

1. 后续学习问答应按主题补充到本文，避免把相互矛盾的新旧说法同时保留为当前结论。
2. 修改控制流、工具协议、技能记录语义、数据分母或模型配置时，必须回源核对代码，并同步修订本文及 `AGENTS.md` 的相关入口。
3. 明确区分代码事实、当前配置、概念解释、实验观察和待验证假设。离线测试、preflight、正式五轮和最终评估不能互相替代。
4. 完整流程图发生变化时，应同步修改文字问答，不能只改图或只改正文。
5. 重要修订应记录到文末维护日志；必要时让独立 agent 审计，主 agent 仍需回源复核。
6. 当前状态与真实实验结果分别查看 `reproduction/PROGRESS.md`、`web/RESULTS.md`；本文主要承担机制学习，不冒充实验结果报告。

## 1. 先建立整体认识

RSI-Flow 不是简单地“用一个模型训练另一个模型”，而是让 Meta agent 通过可测量实验改进完整的 Task agent。

```text
Task agent 状态 = Qwen checkpoint + 可执行 Harness + task Artifacts
Meta agent     = gpt-6-astra + 持久 Codex 线程 + 工具 + Meta 技能记忆
```

其中：

- Qwen checkpoint 提供语言理解、推理与生成能力；
- Harness 组织提示、检索、工具调用、执行与结果处理等任务求解过程；
- Artifacts 是任务相关产物，当前协议包括可修改并直接重评分的 submissions；
- gpt-6-astra 负责 Meta 的推理与决策，当前配置不训练它的权重；
- Codex 持久线程和工具层负责把 Meta 的决策变成实际文件、任务运行和事实回执；
- Meta 技能记忆保存“如何改进 Task”的历史经验，而不是 Qwen 的模型参数。

当前发布是 skill-based variant：Meta 的实验方法论与验收协议固定，跨 attempt、跨轮积累的是经验内容和 Task 状态。操作库不是封闭白名单，Meta 可以按协议修复或新增 operation，并直接使用原生 Codex 工具；这不等于允许 Meta 自主改写单组件选择、同批配对、严格正增益和事实沉淀等核心实验规则。

## 2. 问答一：父子评估与 Meta 经验学习是否构成闭环

### 问题

Task agent 跑完数据后得到轨迹，Meta 分析轨迹并提出改进；执行后比较子结果与父结果；Meta 再分析改进是否有效，并把成败沉淀到 skills 中，从而提升 Meta 的改进能力。这样理解对吗？

### 回答

整体理解正确，但有两个重要校正：

1. 候选主要由 Meta 通过工具构造，Task agent/运行环境负责执行；不是 Qwen 自己决定如何改进自己。
2. Meta 的“学习”主要来自持久经验的积累、检索和维护，不是 Astra 权重训练；经验增多也不保证改进能力单调提升。

核心闭环为：

```text
父代 Task 在当轮任务上运行
    → 获得完整结果和轨迹
    → Meta 读取统计、代表性轨迹和历史经验
    → Meta 选择一个组件并构造一个候选
    → 候选在相同任务批次上评估
    → 比较父子成功率与逐任务差异
    → Meta 明确接受候选或保留父代
    → 无论接受与否都保存事实经验
    → 下一次 attempt/round 检索这些经验
```

按照实验协议，只有完整配对结果的成功率（默认指标 `macro_success`）具有严格正增益，Meta 才应激活候选。这是 Meta 必须遵守的验收规则，不是 `activate_task` 内部的自动正负守卫。候选被拒绝时保留父代，在同一轮开启新的 attempt，不进入下一轮。

## 3. 问答二：Task 侧是智能体，还是只有 Qwen

### 问题

Task 侧应该是一个智能体，不只是 Qwen；是否可以理解为 `agent = Qwen + Harness`？

### 回答

“Qwen + Harness”抓住了主要执行结构，但完整实验状态还要包括 Artifacts：

```text
Task agent = Qwen checkpoint + Harness + Artifacts
```

因此 Task 侧有三类不同改进：

| 组件 | 改进内容 | 是否训练 Qwen |
|---|---|---|
| `MODEL` | 用父代成功轨迹进行 positive-only LoRA SFT，合并新 checkpoint | 是 |
| `HARNESS` | 改进提示、规划、工具调用、记忆、执行 wiring 等 | 通常否 |
| `ARTIFACTS` | 修改已有任务提交并直接重评分 | 否 |

同理，Meta 侧也不只是一个模型。把 Meta 简称为 Astra 时，指的是其模型推理部分，不应忽略持久线程、工具和技能记忆。

## 4. 问答三：Meta 如何调用工具构造候选

### 4.1 调用链

Astra 在持久 Codex 线程中发起原生工具调用，桥接层负责执行并返回事实：

```text
Astra / gpt-6-astra
    │ native tool call
    ▼
Codex app-server
    ▼
NativeExperimentTools
    ▼
controller_tools.py
    ├─ 文件、补丁、技能记忆、比较、快照等操作
    └─ TaskAdapter：rollout / SFT / Harness / Artifacts / evaluate
```

主要动态工具是：

- `experiment`：执行一个实验操作，操作中包含 `operation` 和命名参数；
- `job_status`：查看持久作业状态和日志路径；
- `wait_job`：等待长作业，同时保持原 Meta 会话存活；
- `finish_experiment`：在所有必需产物完成后声明结束，缺项会返回给 Meta。

工具只执行请求并返回回执，不负责选择改进组件，也不自动接受候选。Meta 还可以直接使用原生 Codex 编码工具，并按需修复或新增 operation；这些扩展不改变控制器不选组件、不自动验收的职责边界。

一个物化父代 Harness 的请求示意：

```json
{
  "request": {
    "operation": "materialize_harness",
    "state_path": "round_1/parent_state.json",
    "destination": "round_1/attempts/attempt_1/harness"
  }
}
```

长时间的 rollout、SFT、evaluate 等操作返回 `job_id`。Meta 通过 `wait_job` 和 `job_status` 检查状态；作业返回不等于实验成功，还要检查结果中的真实状态和回执。

### 4.2 HARNESS 候选

典型过程：

1. `materialize_harness` 复制当前父代的完整 Harness。
2. Meta 阅读父代源码、成功/失败轨迹、分数、成本和生产模板。
3. 先写 `module_localization_report.md`，定位有证据支持的问题模块。
4. 再写 `improvement_direction_brief.md`，说明改进机制、适用证据和保留要求。
5. 使用 Codex 原生编辑能力，或 `write_text` / `apply_patch`，生成完整候选。
6. 调用 `rsiflow` 验证和物化；同一候选最多三次可执行检查，失败后先修复再检查。
7. 通过 `compose_task` 将候选 Harness 与继承的父代模型、Artifacts 组成候选 Task 状态。
8. 调用 `run_candidate`，在相同任务批次上评估。

只运行 validator 不等于完成上述 production 流程。

### 4.3 MODEL 候选

1. 从当前父代训练 rollout 中选取经过评估验证的成功轨迹。
2. 调用 `sft`，按有效配置执行 positive-only LoRA SFT。
3. 合并得到新的 Qwen checkpoint。
4. 通过 `compose_task` 与父代 Harness、Artifacts 组成候选 Task 状态。
5. 在相同任务批次上评估候选。

训练失败不能发布候选；不能用基础设施错误、空预测或缺失归属伪装成模型质量零分。

### 4.4 ARTIFACTS 候选

Meta 修改已有 submissions，通过 `artifacts` 相关能力按当前 `direct_submission` 协议直接重评分，而不是重新生成整条轨迹。

### 4.5 比较与激活

- `compare_scores` 报告父子成功率（默认 `macro_success`）、增量、身份字段和轨迹配对事实；
- `compare_task_differences` 报告新增成功、退化、保持、未知项和成本变化；
- `activate_task` 只在 Meta 显式请求时复制所选状态到 active state，不检查增益正负；
- 严格成功率正增益是 Meta 必须遵守的验收协议，工具层不会替 Meta 做组件选择或验收决策。

## 5. 问答四：是否不断优化一个面向 Task 改进的 skill

### 问题

结果写入的是 Meta agent 的技能记忆吗？Meta 原先是否就有一个面向 Task 改进的 skill，然后不断对它优化？

### 回答

写入的是 Meta 的外部持久记忆，但不是“反复修改一个 skill”。更准确的结构是：

```text
固定的实验与经验维护协议
    + 持续积累的 case / rule / procedure / incident
    + 对这些记录进行的追加式修订与维护
```

权威账本为：

```text
meta/skills.jsonl
```

`skills_index.json` 是可以从账本重建的索引，原始轨迹仍保留在 rollout 日志中。技能账本保存摘要和证据引用，不复制完整模型调用历史。

### 5.1 记录类型

- `case`：一次真实干预，包含上下文、诊断、机制、事前预测、实测结果、使用技能、适用范围、未知项和证据。
- `rule`：条件经验，包含 `when`、`recommend`、`avoid_when`、支持证据、反证和置信状态。
- `procedure`：可复用步骤、适用条件、保留要求和版本。
- `incident`：接口、工具或评分事故及修复状态。
- `rule_update`：对规则进行 strengthen、weaken、contradict 或 supersede。
- `skill_maintenance`：对流程进行补充、修订、合并或退休。

每个 attempt 都应写入组件对应的 `skill.HARNESS.<id>`、`skill.MODEL.<id>` 或 `skill.ARTIFACTS.<id>` 案例。只有证据足够时才增加或修订 `principle.<id>`，不是每次强行总结普遍规律。

旧案例事实不因新结论而被覆盖；修订通过追加记录完成。调用次数本身不会自动提高置信度，引用技能不等于理解，关联也不等于因果。

记录生命周期需要进一步区分：`rule_update` 追加规则修订；procedure 的 `revise` 创建带 `previous_version` 的新版本，并标为未验证修订，旧版本支持证据只保留为 `prior_version_support`，不能冒充新版本的实测支持；`supplement` 只补充支持或反证，不自动升级版本或置信度。`revise`、`merge`、`retire` 可以改变默认检索中的 active 记录，但历史仍保留，可通过 `include_inactive=true` 或 `read_skill` 查阅。

### 5.2 初始记忆是否为空

当前 v5 机器配置为：

```json
"meta_skill_seed_path": null
```

因此新正式实验默认从空经验账本开始，但 Meta 仍有固定的实验协议、工具使用说明和技能维护方法。

仓库提供可选的 `RSIFlow/meta_skills/reviewed_seed.jsonl`，含三个 reviewed cases、三条条件规则和两个 incidents；当前配置没有启用。比较不同实验时，必须说明 Meta 是 empty 还是 seeded，不能把两种初始条件混为一谈。

如需在新实验中显式启用种子，实际 CLI 参数是 `--skill-seed`。`RSIFlow/META_SKILLS.md` 当前仍写 `--meta-skill-seed`，这是已知文档差异，应以 `launch_meta.py` 的 argparse 为准；该参数只初始化新账本，resume 不会重放种子。

### 5.3 后续如何使用经验

```text
当前失败 fingerprint
    ↓
retrieve_skills
    ↓
返回相关案例、规则、流程、支持证据与反例
    ↓
Meta 判断适用性，必要时 read_skill 阅读分页详情和原始证据
    ↓
record_skill_use 冻结实际引用 ID、目标错误与预期改进
    ↓
构造并评估候选
    ↓
比较事前预测与实测结果
    ↓
append_skills / maintain_skills
```

检索是有字符和类别预算的条件匹配，不是一个已经训练好的收益预测器。空记忆或没有匹配也是合法结果。

## 6. 问答五：固定元改进方法论与动态状态的关系

可以把系统分成三层：

### 第一层：固定的元改进机制

包括诊断、单组件选择、事前预测、同批配对评估、严格正增益验收、事实记录、技能检索与维护规范。

它规定 Meta “如何做实验、如何学习经验、如何改进改进过程”，在当前 skill-based 版本中保持稳定。固定的是方法论与验收协议，不是操作实现的封闭集合；Meta 可以修复或新增 operation，但不能因此绕过核心实验规则。

### 第二层：动态 Meta 经验

包括 `meta/skills.jsonl`、结构化上下文和可重建索引。它记录 Meta 对 Task 改进的历史认识，是 Meta 的经验记忆，不是 Task 自己的模型权重。

### 第三层：动态 Task 状态

包括 Qwen checkpoint、Harness 和 Artifacts，是 Meta 的改进对象。如果 Task 内部有记忆机制，通常属于 Harness/Artifacts 的组成部分；它与 Meta 的“如何改进 Task”的技能记忆不是同一层。

一句话概括：

> 固定的元改进机制规范实验与经验沉淀；Meta 动态技能记忆记录如何改进 Task；Task 的模型、Harness 和 Artifacts 是被持续改进的对象。

“经验更丰富”是机制允许的结果；“Meta 改进能力确实提高”则需要在固定 Task 状态、新任务批次、相同 API 与预算下做受控比较，不能仅凭账本增长或离线测试宣布成立。

## 7. 正式一轮 B_r 的展开流程

下面是正式演化流程，不是当前把推理、评分、训练和 Meta 链路拆开验证的 preflight。

```text
┌─────────────────────────────────────────────────────────────────────┐
│                  固定层：Meta 元改进方法论                          │
│  观察证据 → 诊断 → 单组件选择 → 事前预测 → 构造一个候选             │
│  → 同批配对评估 → 接受/保留 → 事实沉淀 → 经验维护 → 后续检索        │
│  方法与验收协议固定；操作实现可扩展；Astra 权重不训练               │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Meta Agent = gpt-6-astra + 持久 Codex 线程 + 工具 + 技能记忆        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ retrieve_skills
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Meta 动态记忆：cases / rules / procedures / incidents / revisions   │
│ skills.jsonl + 可重建索引 + 结构化 context + 原始证据引用           │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
╔═════════════════════════════════════════════════════════════════════╗
║ B_r：使用当前轮冻结的 600 个训练任务                                ║
╚══════════════════════════════┬══════════════════════════════════════╝
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 父代 Task = Qwen checkpoint + Harness + Artifacts                   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ run_parent
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 父代完整 rollout 与官方评分                                         │
│ 输出：成功率、逐任务成败、成本、轨迹、任务/数据源身份                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ prepare_meta_evidence
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Meta 决策证据                                                       │
│ 完整统计 + 最多 48 条代表性成功/失败轨迹                            │
│ + 全量轨迹索引 + failure fingerprint + 历史技能与反例               │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Astra：诊断瓶颈并选择 HARNESS / MODEL / ARTIFACTS 之一              │
│ 保存 decision.json；record_skill_use 冻结引用技能和事前预测         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│ HARNESS           │ │ MODEL             │ │ ARTIFACTS         │
│ 物化父代 Harness  │ │ 选择父代合格成功  │ │ 修改已有          │
│ → 模块定位报告    │ │ 轨迹              │ │ submissions       │
│ → 改进方向报告    │ │ → positive-only   │ │ → 按协议直接      │
│ → 完整候选生成    │ │   LoRA SFT        │ │   重评分          │
│ → rsiflow 验证    │ │ → merge 新模型    │ │                   │
└─────────┬─────────┘ └─────────┬─────────┘ └─────────┬─────────┘
          └─────────────────────┼─────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 候选 Task = 新/旧 Qwen + 新/旧 Harness + 新/旧 Artifacts            │
│ 每次 attempt 只改变所选组件，其余继承父代                           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ run_candidate / 协议规定的直接重评分
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 候选在相同 B_r 上评估                                               │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ compare_scores + compare_task_differences                           │
│ 成功率与增量、新增成功、退化、保持、未知项、成本与配对身份            │
│ 并对照 record_skill_use 中的事前预测                                │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
                      ┌────────────────┐
                      │ Meta 显式决策  │
                      └───────┬────────┘
              严格正增益      │       无提升、退化或证据不可信
             ┌────────────────┴────────────────┐
             ▼                                 ▼
┌──────────────────────────┐       ┌──────────────────────────┐
│ 接受候选                 │       │ 保留父代                 │
│ activate_task            │       │ 不激活、不进入下一轮     │
│ 子代成为新 active Task   │       │ 同轮开启新 attempt       │
└────────────┬─────────────┘       └────────────┬─────────────┘
             └────────────────┬─────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 每个 attempt 都执行，不论接受或拒绝                                 │
│ append_skills：事实案例、预测对照、结果、决策、边界、证据引用       │
│ maintain_skills：有证据时新增/修订规则和维护 procedures             │
│ snapshot_task_meta：Task 组件版本 + Meta 记忆 + 组合关系            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
              ┌────────────────┴──────────────────┐
              ▼                                   ▼
┌──────────────────────────────┐     ┌──────────────────────────────┐
│ 拒绝：保留父代，回到同一 B_r │     │ 接受：子代成为下一轮父代     │
│ 读取新增失败经验，继续尝试   │     │ B1 → B2 → B3 → B4 → B5       │
└──────────────┬───────────────┘     └──────────────┬───────────────┘
               └──────────────┐       ┌─────────────┘
                              ▼       ▼
                   ┌────────────────────────┐
                   │ 更丰富的 Meta 经验参与 │
                   │ 下一次检索与决策       │
                   └────────────────────────┘

B5 最终快照接受后
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 独立最终评估，仅用于报告                                            │
│ 当前计划：694/744 scored，ACE 50 pending                            │
│ 不参与 SFT、候选接受或 Meta 技能学习                                │
└─────────────────────────────────────────────────────────────────────┘
```

这里有两个嵌套循环：

- Task 进化循环：父代 → 候选 → 配对评估 → 接受或保留。
- Meta 经验循环：检索经验 → 记录预测 → 干预 → 对照结果 → 写回和维护经验。

候选被拒绝也会丰富 Meta 经验，但不会改变当前有效 Task；候选被接受才会让子代成为后续轮次的父代。

## 8. Preflight 与正式一轮不能混淆

当前 preflight 的目的，是在正式五轮之前分别验证：

- 真实 Qwen 推理与官方评分；
- 固定训练域任务上的 rollout 与回执；
- 有自然正例时的 positive-only SFT、merge、reload；
- 清理与恢复；
- Meta API、原生动态工具和同线程恢复等独立控制链路。

训练侧最小 smoke 不调用 Meta，也不运行正式 `pipeline.run`。其中固定 S2 训练只是验证模型更新机制，不是 Astra 做出的正式 `MODEL` 改进决策。只有正式 B1–B5 才把上述父子评估、Meta 决策和经验学习串成完整闭环。

### 8.1 DeepCoder function 16 canary 的受限闭环

当前 canary 复用真实 Meta/Task 控制流，但故意缩小范围，不能把正式流程图的所有后续步骤套到它身上：

```text
A0 保存初始状态（不做 baseline validation）
  → B1 父代运行固定 16 条 DeepCoder/TACO function
  → Meta 读取证据并只选择 HARNESS / MODEL / ARTIFACTS 之一
  → 构造一个 attempt_1 候选并在相同 16 条任务上比较
  → 显式 accept 或 retain_parent
  → append skills + final context + snapshot
  → 完成；无独立 validation、无下一轮
```

其 `experiment_scope=deepcoder_canary` 使 active domain 只有 code，有效 quotas 为 `0/16/0`；launcher 只在 canary 分支保留 source 的零配额并用正数子集复用原总数校验，multidomain/EnvScaler 的正整数要求没有放宽。`retry_rejected_from_round=2` 让第 1 轮 retain 成为 terminal `completed_retained`，不是回到组件选择。accept 也只能结束这个一轮 canary，不能据此进入 B2。

组件由 Meta 在真实运行时选择，离线配置没有预选 MODEL。只有实际 decision 为 MODEL、父代训练 rollout 有经评估验证的自然成功轨迹且 SFT/merge 回执真实存在时，才可记录 positive-only SFT；零自然正例时必须记 blocked/not run。HARNESS/ARTIFACTS 结果不得改记为 MODEL/SFT，retain 也是有界的有效工程结果而非失败伪装。

这个 canary 不是五轮、3000 任务或最终 694/744，也不产生独立 benchmark 分数。**真实受限闭环现已完成**：Astra 选择 HARNESS，同一 candidate 在 2/3 次检查后 passed；父代 51、候选 93 次真实 Qwen calls，父 7/候选 8 个有效 submission 进入 full verifier。完整同批配对为 3/16=0.1875→4/16=0.25，delta +0.0625，并完成 accept/activate、skills/context/snapshot 和 cleanup。

结果必须连同限制一起理解：4 个新成功伴随 3 个回归，原 3 个成功全部回归；4 个新成功全来自父代零调用失败，3 个回归全为候选零调用失败，过程异常使 Harness 因果归因受限。该单轮 canary 的 MODEL 未选择、SFT 未运行；522 passed/2 skipped 是当时的历史测试计数。执行与证据要求见 `reproduction/audit/reviews/deepcoder-canary-runbook.md`，真实结果见 `reproduction/audit/reviews/deepcoder-canary-real-results.md`。

### 8.2 后续独立单域五轮已经完成

在上述单轮 canary 之后，另一个独立 run 已在**同一批 16 条** DeepCoder/TACO function 上完成 A0+B1–B5：B1/B3/B4/B5 的 ARTIFACTS 均获接受并激活，B2 MODEL 真实完成 20 samples、4 ranks、1 epoch、3 optimizer steps 的 positive-only SFT，但因 4/16→3/16 而 retain_parent、未激活。最终 active 为 base Qwen3-4B + 四个累计 artifacts，固定批总体口径为 2/16→5/16。

这是 80 rollout positions、16 个唯一任务的单域工程闭环，不是正式五域 3000、held-out、694/744 或作者原始实验复现。五轮功能工作树最终完整离线套件为 **1031 passed, 2 skipped**；逐轮证据和限制见 `reproduction/audit/reviews/deepcoder-five-round-real-results.md`，学习入口见 `reproduction/STUDY_GUIDE.md`。

## 9. 数据与证据边界

- 正式每轮训练批次为 600，五轮共 3000 个不同任务。
- 最终独立评估协议为 744；当前 ACE 50 pending，因此计划报告 694/744。
- 独立最终评估只用于报告，不进入训练、候选接受或 Meta 经验学习。
- 当前数据属于公开数据的同协议重构，不能直接宣称精确复现作者原始任务队列或报告分数。
- Meta 经验增长、离线测试通过、工具返回 completed，都不等于正式能力增益。
- 当前文档解释代码机制；真实五轮状态以本节和结果报告为准，不能把单域工程完成扩张解释为正式五域或最终评估完成。

## 10. 推荐代码阅读顺序

1. `AGENTS.md`：项目级控制流、数据协议、GPU 与证据边界。
2. `RSIFlow/persistent_meta.py`：持久 Codex 线程、动态工具、长作业和主循环提示。
3. `RSIFlow/controller_tools.py`：操作分发、Harness 物化、比较、激活与技能工具。
4. `RSIFlow/task_adapter.py`：有效配置与 rollout/SFT/Harness/Artifacts/evaluate 适配。
5. `RSIFlow/skill_memory.py`：账本、检索、索引、规则修订与维护语义。
6. `RSIFlow/META_SKILLS.md`：案例、规则、流程、预测对照和初始化协议。
7. `RSIFlow/prepare_600_744.py`、`data_protocol.py`、`evolution_protocol.py`：冻结数据与实验约束。
8. `reproduction/configs/train_4b_v5.json`：当前机器配置，包括空的技能种子设置。

## 11. 维护日志

### v0.5 — 2026-11-22

- 新增 `reproduction/STUDY_GUIDE.md` 作为系统学习入口，本文继续维护机制问答。
- 补充独立单域五轮已经完成、B1–B5 决策、B2 SFT 未激活和最终 2/16→5/16 的准确口径。
- 保留单轮 canary 与旧测试计数的历史语义；正式五域、held-out、694/744 和作者原始实验复现仍未运行。

### v0.4 — 2026-11-17

- 最终运维同步将当前完整离线套件更新为 **522 passed, 2 skipped**；v0.3 的 517/2 保留为当时真实结果发布的历史计数。
- 本次只同步最终状态，不改变 canary 的 HARNESS、3/16→4/16、4 新成功与 3 回归或因果归因限制。

### v0.3 — 2026-11-17

- 根据真实 run 与独立结果审阅，把 DeepCoder function 16 canary 从“仅离线、未运行”更新为已完成的受限闭环：HARNESS、2/3 检查、51/93 calls、3/16→4/16、accept/activate 和完整终局。
- 同步 4 新成功、3 回归、原成功全回归及零调用过程异常导致的归因限制；保留 full verifier 父 7/候选 8 的准确口径。
- 当前完整离线套件更新为 517 passed, 2 skipped；五轮、正式 SFT、694/744、独立 benchmark 和作者原始实验复现仍未运行。

### v0.2 — 2026-11-17

- 补充 DeepCoder function 16 canary 的 code-only 配额、单 attempt terminal-retain、无独立评估和实际组件/SFT 证据边界。
- 链接 dev4 执行 runbook；明确当前仅离线验证，未运行真实 canary、五轮或 694/744。

### v0.1 — 2026-11-17

- 初始整理父子评估、Task/Meta 组成、工具构造候选、技能记忆与固定元改进方法论。
- 加入正式 B_r 展开流程图及 preflight/正式实验边界。
- 独立 Kimi 审计见 `reproduction/audit/reviews/learning-kimi-r1.md`：无 Critical/Important，两项 Minor；主 agent 已回源确认并补充 procedure 版本、证据隔离、inactive 检索语义和实际 seed CLI 参数。
- 独立 Qwen 审计见 `reproduction/audit/reviews/learning-qwen-r1.md`：一项 Important、四项 Minor；主 agent 已回源确认并澄清方法论固定但操作库可扩展、`macro_success` 成功率判据、`activate_task` 无自动正负守卫、完整 seed 路径及 `compose_task`。
- Qwen scoped 复审见 `reproduction/audit/reviews/learning-qwen-r2.md`：PASS，首轮 Qwen 与 Kimi 的全部 findings 均已 ADDRESSED，无新增问题。
- Codex 审计因上游连续 `429 Too Many Requests` 中断，未生成报告，不计为 PASS 或失败结论；最终有效独立审计为 Kimi 与 Qwen 两份。
