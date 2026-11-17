PASS

# 真实 preflight 失败后最小修复 scoped review（Qwen）

- 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`
- 范围：仅审查未提交 diff 中 `reproduction/scripts/model_scoring_smoke.py`、`reproduction/scripts/training_side_smoke.py` 及对应 tests；核对三项指定问题。
- 真实证据（用户提供）：评分作业 `pt-y48xy4zl` 的 `predictions/*/gpu_0/worker.log` 在 `load_task(frozen['task_state'])` 处把**路径字符串**传给 `dict()` 失败，`model_calls=0`；训练 S1 `pt-ave60pvr` 因未显式设置 bwrap 落入 legacy Landlock，cleanup 均成功。
- 性质：只读。未修改任何文件（除本报告）；未启动实验/SCO/API；未读取凭据。git 以属主身份进程级执行（`setpriv --reuid=10045 --regid=60201 --clear-groups env HOME=/tmp`），未改 trust/全局配置。离线单元测试仅产生 `/tmp` 临时产物（`TMPDIR=/tmp`、`PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`），非实验。

## 结论

**PASS**：0 Critical，0 Important，2 Minor。两项修复均命中根因、符合生产合同、fail-closed 且清理语义正确；测试真实覆盖根因而非掩盖。

## 一、task_state 改为 asdict(state) + task_state_path（model_scoring_smoke.py）

**verdict：正确，符合生产合同（根因修复）。**

- diff：`model_scoring_smoke.py:207-208`
  - 旧：`"task_state": str(state_path), "state_hash": task_hash(load_task(json.loads(state_path.read_text())))`
  - 新：`"task_state": asdict(state), "task_state_path": str(state_path), "state_hash": task_hash(state)`
- 根因回源：`durable.py:28-31` `load_task(value)` 第一行 `value = dict(value)`。旧 binding 把 `task_state` 存为**路径字符串**，消费方 `load_task(frozen['task_state'])` 即 `dict("/path/...")` → `ValueError`，与 `pt-y48xy4zl` worker.log 失败一致。
- 生产合同（所有消费方均把 `frozen['task_state']` 当 dict 传入 `load_task`）：
  - `validate.py:61` `state=load_task(snapshot['task_state'])`
  - `report_predictions.py:52` `state = load_task(frozen['task_state'])`（即 predictions worker 失败点）
  - `harness_api.py:25` `self.state=load_task(frozen['task_state'])`
  - `evaluate_bfcl_frozen.py:158` `state = load_task(frozen['task_state'])`
  - **规范格式佐证**：`evaluate_bfcl_frozen.py:343` 生产自身构造 frozen 用 `'task_state': asdict(state), 'state_hash': task_hash(state)` —— 与新修复**逐字一致**。
- 类型/import 核实：`model_scoring_smoke.py:22` `from dataclasses import asdict`、`:35` 导入 `load_task/task_hash`、`:40` 导入 `TaskAgentState`；`:193` `state = TaskAgentState(...)`，故 `asdict(state)` 为合法 dict、`task_hash(state)` 入参为 TaskAgentState。
- state_hash 一致性：`task_hash`（`durable.py:34-36`）= `value_hash({'state': asdict(state), 'harness': digest(harness_path), 'artifacts': artifact_manifest(...)})`。`state_path = save_json(asdict(state))`，`load_task(asdict(state))` 经 `ArtifactState` 往返重建等价状态，`asdict` 不变，故新 `task_hash(state)` 与旧 `task_hash(load_task(...))` 数值一致；`validate.py:62` 重算 `task_hash(load_task(snapshot['task_state']))` 仍与存储 `state_hash` 匹配。无回归。
- 其余 binding 字段（`system_snapshot_sha256=file_hash(state_path)`、checkpoint_files、task_harness_identity、protocol_hash、source_role/purpose 等）未变，仍满足 `validate.py:62` 所需。新增 `task_state_path` 为冗余可追溯字段，不破坏合同。
- 测试核实：`test_model_scoring_smoke.py:291-300` 改为断言 `isinstance(binding["task_state"], dict)`、`smoke.load_task(binding["task_state"])` 往返（generation/checkpoint_path）、`task_state_path` 文件存在且 `json.loads == binding["task_state"]`、`frozen_path` 内容 == binding。**直接复现并锁定根因合同**（dict 而非路径），非掩盖。实跑 `2 passed`（含本测试）。

## 二、execute_rollout 强制 bwrap（training_side_smoke.py）

**verdict：fail-closed 正确，清理语义正确。**

- diff：`training_side_smoke.py:804-806`，在 `with _signal_guard():` 内、`TaskAdapter(...)`（:807）/bootstrap/`_assert_registry_owned`（:816）/`ensure_services`（:817）**之前**：
  `if os.environ.get("RSIFLOW_SANDBOX_BACKEND") != "linux_bwrap_seccomp_v1": raise SmokeBlocked("Real execution requires explicit RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1")`
- fail-closed：任何 adapter/bootstrap/service 副作用前即拒绝；未设/错设 backend → `incomplete`，绝不落入 legacy Landlock。命中 S1 `pt-ave60pvr` 根因。
- 清理语义：guard 在 `try` 内抛出 → `except` 记 error → `finally` `cleanup_owned_services(config, root)`。此时尚未 `ensure_services`，registry 不存在 → 返回「no owned registry created / nothing to stop」、`services_stopped=True`（无操作），`_finish` 写 `incomplete` receipt。**不虚报已停服务、不误杀外部进程**，与真实 S1 cleanup 成功语义一致。
- 测试核实：`test_training_side_smoke.py:493-507` `test_rollout_requires_explicit_bwrap_before_bootstrap`：`monkeypatch.delenv` 移除 backend，`ForbiddenAdapter.__init__` 与 `ensure_services` 均 `pytest.fail`（一旦被构造/调用即失败），断言 `status=="incomplete"` 且 error 含守卫消息。**证明守卫先于一切副作用**，真实覆盖根因。实跑通过。
- autouse fixture `_project_root`（:91-95）新增 `monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND","linux_bwrap_seccomp_v1")`：使其余测试在**正确生产条件**下运行；新测试显式 delenv 单独验证守卫。设置生产所需环境变量属合理夹具，非掩盖（掩盖会是「绕过守卫仍断言成功」，此处相反）。

## 三、测试是否掩盖根因

**verdict：未掩盖，真实覆盖。**

- model 测试断言 dict 类型 + `load_task` 往返 + `task_state_path` 一致性，直接对应 `dict(value)` 合同；若回退为路径字符串则 `isinstance(...,dict)` 与 `load_task` 均失败。
- training 测试以「调用即失败」的 ForbiddenAdapter/ensure 证明守卫位置先于副作用；若守卫被移除或后置，ForbiddenAdapter 会被构造 → 测试失败。
- 两测试实跑 `2 passed in 1.53s`（离线、tmp 产物）。

## Minor

### M1 — backend 字符串字面量硬编码，建议共享常量

- 证据：`training_side_smoke.py:804/806` 直接写字面量 `"linux_bwrap_seccomp_v1"`；`model_scoring_smoke.py` 使用 `BACKEND` 常量。
- 影响：仅维护性/一致性，非功能缺陷。建议training_side_smoke 复用同一常量（或从共享模块导入），避免两处漂移。

### M2 — bwrap 守卫为 rollout-only，宜补注释说明 rationale

- 证据：守卫仅在 `execute_rollout`（:804）；`execute_sft`（:988）/`execute_recovery`（:1068）无该守卫。
- 核实：training_side_smoke 中 S2/S3 仅经 `LocalTaskClient`（:42/958-961）做推理短生成与训练，**不调用候选/native sandbox**（全文无 `LinuxSandbox`/`HarnessAPI`/`run_candidate`/`generate_dynamic`）；候选 Harness 的 sandbox 隔离只发生在 rollout。故 rollout-only 守卫**恰当**，非遗漏。
- 建议：在守卫处补一行注释说明「仅 rollout 执行候选/native sandbox，S2/S3 为训练+推理，不需 bwrap 候选隔离」，防止后续误以为 S2/S3 漏守卫或误加。非阻塞。

## 剩余限制

- 本审查为静态回源 + 离线单元测试，**未运行**真实评分/训练/SCO/API；`pt-y48xy4zl`/`pt-ave60pvr` 的真实修复后重跑需另行授权执行以最终确认 worker.log 不再失败、S1 不再落 legacy Landlock。
- 仅审查指定两脚本与对应 tests 的未提交 diff；未重做全量审计，未核对其它脚本/配置。
- 行号基于当前未提交工作树状态；提交后若代码变动须重核。
- 未读取凭据；git 仅属主进程级 diff/rev-parse，未改 trust/全局配置。

## 建议

- 两项根因修复正确，可进入主 agent 回源复核与（另行授权的）真实重跑验证。
- 可选：M1 共享 backend 常量、M2 补 rollout-only rationale 注释（均非阻塞）。
