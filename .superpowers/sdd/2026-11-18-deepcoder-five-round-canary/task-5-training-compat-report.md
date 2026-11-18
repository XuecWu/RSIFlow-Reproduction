# Task 5 Transformers warmup 生产兼容修复报告

- 日期：2026-11-18（UTC）
- 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round`
- 实现 commit：`45598ee7e0cdb5e95e18759fde753062af9b72c2`（`fix: support transformers v5 warmup scheduling`）
- 范围：仅修改 `RSIFlow/runtime/scripts/train_task_meta_sft.py`，新增 `RSIFlow/tests/test_training_script_compat.py` 与本报告；未改依赖、smoke 现场或 `requires_audit` 状态。

## 根因与修复

Retry 4 的真实环境为 Transformers 5.22.2，其 `TrainingArguments` 只有 `warmup_steps`，直接传入 `warmup_ratio` 会在优化 step 前抛出 `TypeError`。项目声明 `transformers>=4.51`，因此不降级共享环境。

实现按 `TrainingArguments` 实际签名选择合同：

- v4：继续传 `warmup_ratio=ratio`，scheduler 不重写 `warmup_steps`。
- v5：构造时传 `warmup_steps=0`，并把原 ratio 交给 `AuditedTrainer.create_scheduler`；在调用父类前按实际 `num_training_steps` 设置 `ceil(num_training_steps * ratio)`。
- `warmup_steps_for_ratio` 使用 `math.ceil`；0、0.03、1 均保持原语义，101 steps 与 0.03 得到 4。
- `resolved_training` / `resolved_training_config` 中保留原 `warmup_ratio`，multidomain 默认仍为 0.03；非法 ratio 继续由 `resolve_training_options` 拒绝。
- `training_metrics.json` 新增顶层 `warmup_steps=trainer.args.warmup_steps`，记录 scheduler 后的最终值。

## TDD RED → GREEN

RED 命令：

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_training_script_compat.py
```

实现前结果：`12 failed, 7 passed`。12 个失败均因 `warmup_argument_contract`、`warmup_steps_for_ratio` 或 `create_audited_trainer_class` 尚不存在；既有非法 ratio 校验等 7 项通过。

实现后同一新测试为 `19 passed`。首次 GREEN 有一条本机 NVML 不可初始化警告，仅对真实 `TrainingArguments` 构造测试过滤该已知环境警告；最终 scoped 运行无警告。

覆盖包括：fake v4/v5 签名、实际 Transformers 5.22.2 kwargs 可构造、v4 ratio 不变、v5 延迟换算、0/0.03/1、101 steps ceil=4、非法 ratio、v5 按实际 scheduler steps 设置以及 v4 不重写。

## 回归与静态检查

```text
新测试 + 训练 scoped：
PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests/test_training_script_compat.py tests/test_training_side_smoke.py
→ 170 passed

完整离线套件：
PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests
→ 650 passed, 2 skipped

新测试 ruff：
python -m ruff check RSIFlow/tests/test_training_script_compat.py
→ All checks passed

git diff --check
→ PASS
```

上述 `python` 均为 `/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python`。额外对训练脚本运行 ruff 时报告 `SIM102` 一项和 `E701` 两项；对 HEAD 基线文件复现同样三项，属于既有风格问题，本次未做无关清理。

## 自审与边界

自审确认 v5.22.2 的父类 `Trainer.create_scheduler` 在调用时通过 `self.args.get_warmup_steps(num_training_steps)` 读取 warmup，因此子类在 `super()` 前赋值生效；optimizer 与 `num_training_steps` 原样传递。未发现 Critical/Important 问题。按要求未 dispatch 子 agent，本自审不冒充独立 reviewer。

未运行 CPU/GPU 训练，未运行真实 `execute`，未启动五轮，未删除或重跑 Retry 4 的 incomplete / `requires_audit` 现场。真实 MODEL gate 仍需后续经授权的审计调和与新输出路径验证；本报告只证明离线兼容合同与回归。
