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

---

# Round 1：I1/I2/M1 修复

- 日期：2026-11-18（UTC）
- 输入：`task-5-training-compat-review.md` 的 I1、I2、M1 完整审阅意见。
- 实现 commit：`def5d22afee52949fe4456c0c760629eedb71852`（`fix: report effective trainer warmup steps`）。
- 范围仍仅包括训练脚本、兼容测试和本报告；未改依赖，未访问或修改旧 smoke root。

## I1：按真实签名验收安装环境

`test_installed_transformers_warmup_kwargs_match_signature` 不再无条件要求 v5。测试先读取真实 `TrainingArguments` 签名：存在 `warmup_ratio` 时验收真实 v4 kwargs/ratio 和 `scheduler_ratio=None`；否则验收 v5 `warmup_steps=0` 和延迟 ratio。fake v4/v5 两个分支及原 ratio 断言均保留。

当前实际验证环境仍为 Transformers 5.22.2；没有安装、降级或声称执行过真实 v4 环境。v4 由签名 fake 和穿过生产 `train()` 的受控 v4 用例覆盖，真实 v4 安装会走上述动态真实构造分支，而不会再因 v5-only 断言失败。

## I2：真实 scheduler 与生产 `train()` 接线

新增真实 Transformers `Trainer.create_scheduler` 测试，使用 tiny CPU `torch.nn.Linear`、SGD optimizer、真实 `TrainingArguments` 和生产 `create_audited_trainer_class`：

- 5 steps、ratio 0.4 → `args.warmup_steps=2`，scheduler step 1/2 的学习率为 0.5/1.0；
- 101 steps、ratio 0.03 → `args.warmup_steps=4`，scheduler step 1/2 的学习率为 0.25/0.5。

学习率断言穿过真实 `get_scheduler`，不再只检查 fake 父类调用。该 v5-only 行为测试在真实 v4 签名环境下显式 skip，不破坏声明支持的 v4 测试环境。

另新增穿过生产 `train()` 的受控 v4/v5 参数化测试。spy 验收 `warmup_argument_contract` 与 `create_audited_trainer_class` 确实由 `train()` 调用；受控父类 scheduler 使用 `args.get_warmup_steps()` 验收实际接收值；最终返回报告和落盘 `training_metrics.json` 均验收 `warmup_steps` 与原 resolved ratio。请求采用 one epoch、`max_steps=-1`，而受控训练后的 `trainer.state.max_steps=5`，确保指标不能使用请求中的 -1 代替实际训练步数。该测试不执行真实模型训练或 GPU 工作。

## M1：统一有效 warmup 指标

生产指标改为：

```python
warmup_steps_for_ratio(options["warmup_ratio"], trainer.state.max_steps)
```

因此 v4 非零 ratio 不再把通常仍为 0 的原始 `trainer.args.warmup_steps` 冒充有效步数；v4/v5 都记录 `ceil(actual_max_steps * ratio)`。调度 kwargs、v4 不重写和 v5 父类调用前赋值行为均未改变，`resolved_training` / `resolved_training_config` 仍保存原 ratio。

## TDD RED → GREEN

首次扩展测试运行得到 `2 failed, 21 passed`，失败是受控模型替身把 LoRA 参数误命名为不含 `lora_` 的名称，尚未到达目标断言；修正替身及 optimizer/scheduler 调用顺序后，目标 RED 命令为：

```bash
PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests/test_training_script_compat.py -k train_wires
```

目标 RED 实测：`1 failed, 1 passed, 21 deselected`。v4 用例在 `report["warmup_steps"]` 得到 0、期望 2；v5 用例通过，精确复现 M1。I1 动态构造和 I2 真实 scheduler 属于对现有正确行为的表征测试，在生产指标修改前已通过，不伪称为失败驱动的新生产行为。

修改指标后：

```text
新测试：23 passed
训练 scoped（兼容测试 + training_side_smoke）：174 passed
完整 tests：654 passed, 2 skipped
新测试 ruff：All checks passed
git diff --check：PASS
```

最终完整套件在 one-epoch 测试补强后重新执行，结果仍为 `654 passed, 2 skipped`。自审确认三项均有对应行为断言，未发现新增 Critical/Important；按要求未 dispatch 子 agent。

未运行真实 GPU/execute、真实 CPU 训练或五轮；真实 scheduler 测试仅创建 tiny CPU model/optimizer 并推进 scheduler，生产 `train()` 测试使用受控替身。旧 incomplete / `requires_audit` 现场保持不动，真实 MODEL gate 仍需后续独立 re-review、审计调和、单独授权和新 output root。
