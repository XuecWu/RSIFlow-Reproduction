# 真实 preflight 首轮失败与 fix 1 记录

- 日期：2026-11-17
- 实现基线：`b99818fffea3af161e02fa84d3122aecc2852f48` + 当前四个代码/测试文件的最小 diff
- 性质：真实作业失败诊断与离线修复；不是模型评分通过、训练通过或正式实验结果。

## 真实失败证据

### v5 真实评分 `pt-y48xy4zl`

- SCO 终态：`FAILED`；执行前长期 `STARTING/Pending`，不是排队超时导致代码失败。
- 输出：`reproduction/logs/model-scoring-v5-a100-20261116T204416Z/`。
- `smoke_report.json`：`status=incomplete`、`model_calls=0`、四卡服务 `verified`。
- 四个 code/QA worker 均在 `report_predictions.py:52` 调用 `load_task(frozen['task_state'])`，随后在 `durable.py:29` 对路径字符串执行 `dict(value)`，报 `ValueError: dictionary update sequence element #0 has length 1; 2 is required`。
- BFCL 在同一冻结状态合同上失败，五个 benchmark 均为 blocked，没有任何模型分数。
- 清理：`services_stopped=true`、`four_gpu_memory_released=true`、`cleanup.error=null`。
- 旧目录和 driver log 保留，不覆盖、不改写为模型零分。

### 训练侧 S1 `pt-ave60pvr`

- SCO 终态：`FAILED`。
- 输出：`reproduction/logs/training-side-v5-s1-20261116T224557Z/`。
- `receipts/rollout.json`：`status=incomplete`、`selected_tasks=5`，无正例结论。
- 错误为 `SandboxUnavailable`，诊断字段显示 legacy Landlock ABI `-1`；SCO 命令未设置 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`。
- 清理：owned registry、服务停止和四卡显存释放均通过，`cleanup.error=null`。
- S2/S3 未启动；旧输出保留，不在带 final receipt 的目录上静默重跑。

## 根因

1. `model_scoring_smoke.freeze_smoke_task()` 将 `task_state` 写成状态 JSON 的路径字符串；生产 `validate.py`/`pipeline.freeze()` 使用内联 `asdict(state)`，`report_predictions.py`、`HarnessAPI` 和 `evaluate_bfcl_frozen.py` 也都按内联字典调用 `load_task()`。这是 smoke 适配器的冻结合同错误，发生在模型调用之前。
2. `training_side_smoke.execute_rollout()` 没有像评分 runner 一样显式要求 bwrap 后端；缺失环境变量时进入 legacy Landlock，而不是在 bootstrap/service 前以清晰错误阻断。

## TDD 与修复

- RED：两个聚焦测试均为 `FAILED`。
  - 冻结合同测试复现 `task_state` 不是字典。
  - 缺失 bwrap 环境测试证明 `TaskAdapter` 在阻断前已被构造。
- 修复：
  - `task_state=asdict(state)`，新增 `task_state_path` 保留文件位置；原状态文件 SHA256、checkpoint/Harness/manifest 绑定保持不变。
  - rollout 在 `TaskAdapter`、bootstrap 和服务副作用前要求 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`。
- GREEN：两个聚焦测试 `2 passed`。
- 两个完整测试文件：`148 passed`。
- 主 agent 全量离线测试：`461 passed, 2 skipped in 123.69s`。

## 独立审阅、下一步与边界

- 独立 Qwen scoped review `reproduction/audit/reviews/real-preflight-fix1-qwen.md`：**PASS**，0 Critical、0 Important；两项 Minor 仅为常量复用和 rollout-only rationale 注释，不阻断本次修复。
- 修复提交并推送后，使用全新输出目录和显式 bwrap 环境提交两项有界 A100 smoke。
- 旧失败历史必须保留；新一轮只有真实回执才能关闭评分或 S1 门。
- 离线 GREEN 不证明真实模型预测、官方评分、自然正例、SFT、reload 或恢复已通过。
- 不启动五轮正式演化、正式 SFT 或最终 694/744。
