# DeepCoder 单域五轮工程演化设计

- 日期：2026-11-18（UTC）
- 状态：设计已由用户确认，待书面规格复核与实施计划
- 前置证据：DeepCoder function 16 单域单轮单候选真实 canary
- 执行目标：dev4 独占 4×A800 80GB，共享 `/data/wuxuecheng`

## 1. 目标与结论边界

在既有生产 Qwen Task、Astra Meta、controller tools、真实 verifier、bwrap 隔离、配对评分和技能记忆机制上，完成 A0+B1–B5 的单域五轮工程演化。每轮固定运行同一批 16 条 DeepCoder/TACO function 任务，最多一个实验 attempt 和一个候选。

最终可宣称的上限是“固定 16 条 DeepCoder function 任务上的五轮工程闭环已完成”，且只有每轮必需回执、最终进度、持久作业调和和资源清理全部成立时才可使用。不得宣称：

- 完成五域、每轮 600、五轮 3000 不同任务的正式协议；
- 复现作者报告结果或最终 694/744；
- 模型质量普遍提升、HARNESS 因果改进或 Meta 能力单调增强；
- MODEL/SFT 已运行，除非存在逐轮真实组件与训练回执。

五轮共 80 个训练 rollout 位置，但只有 16 个唯一任务。反复使用同一批任务可能过拟合；本实验没有独立 held-out，因此每轮分数只是固定批配对事实，不是泛化评估。

## 2. 已确认的流程

```text
新的 A0：Qwen3-4B + seed Harness + 空技能记忆
  → B1：当前父代在固定 16 条上 rollout
  → Astra 检索本轮及历史经验，选择一个组件、构造一个候选
  → 候选在同一 16 条上配对评估
  → accept_candidate：激活候选；retain_parent：保留父代
  → 当前轮 skills/context/snapshot 完成
  → 进入 B2，依次推进至 B5
  → 五轮状态、jobs、app-server、服务和四卡清理核验
```

接受仍要求完整同批配对结果严格净正增益，并由 Meta 显式请求激活。控制器只记录事实和执行操作，不自动选择组件或接受候选。

`retain_parent` 是**当前轮终态**，不是整个五轮实验终态。拒绝后不得在同一轮继续重选或创建第二个 attempt；完成当前轮经验与快照后，以未变化的父代进入下一轮。

## 3. 新实验身份与不可变性

新增独立配置、release 和 run，不修改或恢复已完成的单轮 run：

- `experiment_scope=deepcoder_five_round_canary`；
- `rounds=5`、`training_tasks_per_pass=16`；
- `training_schedule=round_repeated`，仅允许该新 scope 使用；
- `allocated_tasks=80` 表示五个轮次的任务位置总数，另记录 `unique_tasks=16`；
- domain quotas 精确为 `tool_use/code/searchqa = 0/16/0`；
- `evaluation_schedule=none`，A0 与 B1–B5 都不要求或允许独立 validation；
- `retry_rejected_from_round=6`，保证 B1–B5 拒绝后不重选，同时允许当前轮完成后推进；
- `meta_skill_seed_path=null`，新实验从空技能记忆开始；
- seed=42、每任务一次 rollout、同一固定顺序。

上一轮 complete run 的 active Task、accepted Harness、skills 和 context 不导入新 A0。上一轮只提供工程经验及独立 MODEL smoke 的已验证自然正例来源。

任何已有 run、attempt、release、审计文件均不得删除或覆盖。恢复必须沿用同一 config、run dir、Astra thread 和 backend，并递增 supervision attempt。

## 4. Frozen release 语义

新增独立 release，五份轮次 manifest 均指向相同的 16 条原始记录，保持相同 ID、顺序和逐 ID source hash。release 元数据必须明确：

- `schedule=round_repeated`；
- `rounds=5`、`tasks_per_round=16`、`unique_tasks=16`、`allocated_task_slots=80`；
- B1–B5 的任务内容重复是经过批准的实验设计，不是绕过 disjoint 校验；
- 每轮 manifest 仍有独立 round identity，用于回执和恢复；
- 原始 canary release、v5 release、源记录 bytes 和检索资产保持不变。

正常 `round_disjoint`、三域配额和 3000/744 校验不得放宽。新 scope 不能通过复制 B1 后仍伪装成 `round_disjoint` 来绕过协议。

## 5. 生产兼容扩展

### 5.1 配置与 active domains

`PipelineConfig` 和 launcher adapter 增加新 scope 与 `round_repeated`。只有新 scope 允许：

- code=16，其他两个 domain 为 0；
- 五轮重复相同 16 个任务；
- `allocated_tasks=rounds*tasks_per_pass`，同时唯一任务数保持 16。

TaskAdapter、executor、SFT provenance 和 release builder 必须一致识别该 schedule。多域、EnvScaler 和原单轮 canary 的既有行为保持不变。

### 5.2 逐轮进度与提示词

新增策略语义 `terminal_retain_per_round`：每轮 retain 后保留 comparison、selection、skills/context/snapshot，并将下一轮设为当前阶段。完成条件为 A0 和 B1–B5 全部必需 milestones 完成。

初始 prompt、persistent instructions、mainline reminder、resume prompt、tool 描述和最终 handoff 必须一致表达：

- 五轮共享一个持久 Astra thread；
- 每轮一个 attempt；
- retain 不激活、不重选，但会进入下一轮；
- 不运行独立 validation；
- B5 完成后才允许 `finish_experiment`。

不得让某一层仍保留“retain 终止整个实验”“fresh round-disjoint tasks”或“无限重试”的旧指令。

### 5.3 组件更新

Astra 每轮可从 HARNESS、MODEL、ARTIFACTS 中选择一项：

- HARNESS 保留模块定位、方向报告、完整 bundle 和最多三次检查；
- MODEL 仅使用当前轮父代自然成功轨迹，遵循 positive-only、one epoch、四 rank、merge、child reload；
- ARTIFACTS 使用 direct submission 重评分，不伪造新轨迹。

MODEL 的真实最小 smoke 是五轮启动前门。若该门未通过，五轮不得带着未验证 MODEL 路径启动；必须保留 blocker，并另行审阅明确限制组件范围的配置，不能由 Meta 临时绕过。

## 6. 多轮控制链路预检

### 6.1 离线真实进度器预检

通过实际 `ExperimentProgress`、persistent instructions 和工具回执处理代码构造隔离临时 run，覆盖：

1. B1 accept 后进入 B2；
2. B2 retain 后完成快照并进入 B3；
3. B3–B5 逐轮推进，B5 前 `finish_allowed=false`；
4. B5 完成后 `finish_allowed=true`；
5. 同一轮第二个 attempt 不被进度语义要求或鼓励；
6. 每轮父子 ID、manifest、round identity 和 active state 链保持可审计。

离线预检不产生真实演化完成结论。

### 6.2 中断与恢复预检

使用隔离临时进程和回执覆盖：

- provider 连续 502 后停止当前 supervision；
- 同 run、同 thread、新 supervision attempt 恢复；
- 已完成的 A0、父代 rollout、候选和 SFT 不重复；
- active durable job 未调和时禁止 resume 和 cleanup；
- launcher PGID、app-server、jobs 与 owned services 分开判定；
- 当前 attempt exit/reconcile 完整后才允许后续动作。

### 6.3 真实环境门

启动前逐项核验 reviewed HEAD、干净执行 worktree、完整离线测试、frozen release、bwrap hash/receipt、Meta 私有凭据存在性、Responses 健康、GPU 0–3、端口、磁盘、socket 和新 output/run。`--check` 返回 0 只是配置事实，不替代真实 GPU/API/sandbox 检查。

### 6.4 MODEL 最小真实 smoke

在五轮 run 外建立独立 smoke 输出，复用上一轮父代中经官方评估成功的自然轨迹。必须逐条绑定源 run、task ID、source hash、parent Task identity 和 trajectory hash，完成：

```text
自然正例筛选 → positive-only LoRA SFT → merge
  → 新 checkpoint identity → 四卡 child reload → 短生成 → 服务停止与清理
```

该 smoke 不是五轮中的候选，不参与 accept、skills 或模型质量结论。若无法安全导入并核验上一轮正例，MODEL 门保持未通过，不改用 held-out、造标签或降低 provenance 要求。

## 7. 运行监督与停止规则

沿用已验证的 detached supervisor。launcher 监督时限不等于 durable jobs、app-server 或 GPU 服务的硬边界；所有 owner 必须分别调和。

出现以下情况时停止当前 attempt 并进入排障，不盲目开启平行 run：

- 持续 Meta provider/transport 失败；
- bwrap、workspace 写门或身份校验失败；
- 父子任务 ID/hash 不一致；
- 同轮出现第二个实验 attempt；
- active jobs 无法调和；
- 真实 MODEL 链路偏离已审阅 smoke；
- 多轮进度与实际回执矛盾。

零调用过程异常必须按轮统计并关联 new success/new regression。不得事后改分、删除失败任务或把过程异常自动改称模型质量；若异常使结果无法解释，可完成工程事实记录，但质量结论保持受限。

## 8. 审计与公开结果

每轮至少记录：

- 实际组件、attempt、candidate 和 executable-check 次数；
- 父子真实 Qwen 调用数、有效 submission/full verifier 覆盖；
- macro success、delta、new success、new regression、retained failure；
- 零调用过程异常类型及其与结果转移的重合；
- accept/retain、active identity、skills/context/snapshot hashes；
- supervision、jobs、服务和 GPU 状态。

主 checkout 既有 dirty 账本保持原样，不覆盖、拼接或借用其他 worktree 的同号 sequence。新操作使用唯一 phase、绝对审计根和脱敏证据路径。原始 prompts/responses、私有轨迹、凭据和大日志不进入公开网页。

公开页面逐轮展示脱敏汇总，并持续标明固定 16 条、重复批次、无 held-out、无 694/744 和非作者复现。

## 9. 验收标准

五轮交付必须同时满足：

- 所有新增离线测试及既有完整套件通过；
- 多轮进度、retain 推进、同 thread 恢复和清理预检通过；
- MODEL smoke 通过，或其阻断与后续受限范围经过单独审阅；
- B1–B5 每轮都有完整必需回执，没有缺轮或用旧结果替代；
- 最终 active state 与最后一次接受结果一致，retain 轮不改变父代；
- 所有 durable jobs 有最终回执，app-server/launcher/services 和四卡清理有实测证据；
- 独立 Spec 与质量审阅关闭 Critical/Important；
- 报告、学习文档、AGENTS、复现手册、审计和网页口径一致。

任一真实轮次缺失时，只能报告实际完成轮次和 blocker，不能把五轮目标写成已完成。

## 10. 非目标

- 不恢复五域正式 preflight 或五轮 3000 任务实验；
- 不修复 HotpotQA/2Wiki 已知问题，除非新代码共享回归直接要求；
- 不运行独立 694/744、ACE、8B 或作者结果对比；
- 不放宽 host-side 写门、seccomp、bwrap 身份或凭据边界；
- 不修改上游官方测试、冻结原始数据或 scorer 来制造通过。
