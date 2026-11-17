# DeepCoder 单域端到端 Canary 设计

日期：2026-11-17
状态：已完成（固定任务数已调整为 16；真实 canary 已完成，终审问题由 final-fix-report.md 闭环）
执行环境：独占 4×A800 80GB dev 机器，共享 `/data/wuxuecheng`

## 目标

用同一套生产 Qwen Task、Astra Meta、controller tools、TaskAdapter、positive-only SFT、配对评分和技能记忆机制，完成一个 DeepCoder/TACO function-only、一轮、固定 16 条任务的端到端工程 canary。

成功结论只允许表述为“单域端到端工程 canary 完成”。不得表述为完成原始三域/五源协议、五轮 3000 任务、完整 694/744 或复现作者结果。

## 已批准流程

```text
固定 16 条 DeepCoder/TACO function 训练任务
  → Qwen 四卡父代 rollout 与官方测试
  → 持久轨迹、评分和回执
  → Astra 分析并构造一个候选
  → 候选在相同 16 条任务上评估
  → MODEL 分支仅用自然成功父代轨迹 SFT、merge、reload
  → 父子比较、接受或保留
  → skills/context/snapshot 与审计落盘
  → 清理和恢复
```

## 范围决策

- 数据源仅为 v5 frozen B1 中 `source=deepcoder_taco` 且 `strata.interface=function` 的任务。
- 选择规则是在任何 canary 模型输出前，按父 B1 manifest 原顺序取前 16 条匹配任务；不得根据结果换题。
- `rounds=1`、`training_tasks_per_pass=16`、`rollouts_per_task=1`、seed 42。
- `experiment_scope=deepcoder_canary`，domain quotas 为 `tool_use=0`、`code=16`、`searchqa=0`。
- `evaluation_schedule=none`：A0 和 B1 都不运行独立 744/694；canary 的父子同批评分不是 held-out benchmark。
- 最多一个 attempt。若候选被拒绝，记录 retain、skills/context/snapshot 后以 `completed_retained` 结束，不在同轮无限重试。
- Meta 可自由选择 HARNESS、MODEL 或 ARTIFACTS；只有选择 MODEL 且存在自然正例时才执行 SFT。零正例不得造标签、换题或使用 held-out。
- 新 frozen release 只派生自训练 B1，不修改 v5 manifest、原始 record bytes 或已有验证集。

## 生产兼容扩展

### `PipelineConfig`

新增 `experiment_scope="deepcoder_canary"`。只有该 scope 允许三个 domain key 都存在但 tool_use/searchqa 配额为 0，且 code 配额必须等于 16。现有多域和 EnvScaler 校验不变。

提供统一 active-domain 判定，TaskAdapter 和 pipeline 都据此传 `expected_domains=("code",)`，避免一个接受零配额而另一个仍强制三域。

### 无独立评估日程

`evaluation_schedule=none` 只用于 canary：从 ExperimentProgress 的 A0/B1 required milestones 中移除 validation_snapshot 和 independent_validation，并生成明确的无 744 提示。既有 final_only 与 after_each_committed_round 行为不变。

### 单 attempt 终止

launcher 从机器配置读取 `retry_rejected_from_round`。canary 一轮配置设为 2，使 B1 拒绝不会触发既有“同轮重选”进度重置；无评估日程下，保留父代并完成经验与快照后即可结束。正式多域配置仍默认 1。

### Frozen release 与机器配置

新增独立 builder 和配置：

- `reproduction/scripts/prepare_deepcoder_canary.py`
- `reproduction/configs/train_4b_deepcoder_canary.json`

builder 复用 `prepare_600_744.repack`/`native_index`，逐条核验原 record offset/length/SHA256，写独立 B1 manifest、tasks.sqlite、selection 和 READY；源 B1 与检索资产前后哈希不变。

## 验收证据

### 离线

- 新 scope 仅允许 code=16、其他域为 0；多域零配额仍拒绝。
- TaskAdapter/pipeline active domains 均为 code-only。
- none 日程移除验证里程碑；现有 final_only 测试不变。
- canary prompt 不含执行 744/A0 validation 的要求，并明确单 attempt、retain 可结束。
- release 16/16 均为 DeepCoder function，顺序确定，原 bytes/hash 不变。
- 全套既有测试通过。

### 真实

- 四卡 Qwen 服务身份、checkpoint/Harness/manifest 绑定通过。
- 父代和候选均恰好覆盖同一 16 条任务，官方代码测试分母不缩减。
- Meta 持久线程、decision、skill_use、一个 candidate、paired comparison 和 accept/retain 回执齐全。
- MODEL 分支若发生：正例仅来自父代、四 rank、one epoch、merge、child reload 和短生成证据齐全。
- 每次 attempt 的 skills/context/snapshot 落盘；拒绝保留父代也可成为有效 canary 终态。
- 清理只停本 canary owned registry；四卡释放；恢复不重复 SFT。
- 明确记录实际执行了哪个组件，不能把 HARNESS/ARTIFACTS 成功写成 MODEL/SFT 成功。

## 非目标

- 不修复 HotpotQA EAGAIN 或 2Wiki gold 类型问题，除非单域 canary 直接依赖。
- 不运行五轮、其他数据源、正式 694/744 或作者结果对比。
- 不修改上游官方测试断言、frozen 原始数据或评分器来制造通过。
- 不在仓库、网页、命令行或审计中写入 API key。
