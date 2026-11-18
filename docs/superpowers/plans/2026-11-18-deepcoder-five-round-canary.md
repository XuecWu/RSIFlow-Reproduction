# DeepCoder 单域五轮工程演化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立新 run 中完成固定同一批 16 条 DeepCoder/TACO function 任务的 A0+B1–B5 真实工程演化，每轮最多一个候选，并提供 MODEL 预检、恢复、审计和脱敏结果。

**Architecture:** 新增 `deepcoder_five_round_canary` 与 `round_repeated`，复用 TaskAdapter/RoundStore 的逐轮执行，不改变多域协议。frozen release 以一份 SQLite 索引和五份独立 manifest 表达重复任务，避免 SQLite `task_id` 唯一键冲突；PersistentMeta 的五轮策略只允许当前轮 terminal retain 后推进。真实 MODEL smoke 独立导入上一轮 A0/parent 自然正例，不导入 accepted candidate 或 skills。

**Tech Stack:** Python 3.11、pytest、Pydantic、现有 RSIFlow controller/runtime、Qwen3-4B、Astra Responses、4×A800、Linux bubblewrap/seccomp、JSONL 审计、静态 web。

**Spec:** `docs/superpowers/specs/2026-11-18-deepcoder-five-round-canary-design.md`

## Global Constraints

- 每轮固定同一 16 条 function，五轮共 80 个 rollout 位置、16 个唯一任务；不得换题。
- 新 scope=`deepcoder_five_round_canary`、schedule=`round_repeated`、rounds=5、quotas=`0/16/0`、retry_rejected_from_round=6、evaluation_schedule=none。
- 新 A0 使用 Qwen3-4B、seed Harness、空 skills；上一轮 complete run 保持只读。
- 每轮一个实验 attempt；retain 完成当前轮 skills/context/snapshot 后进入下一轮，不重选。
- 真实执行显式 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`，不放宽写门或回退 host/Landlock/danger-full-access。
- 不运行五域、3000 任务、694/744、ACE、8B 或独立 benchmark。
- 主 checkout 既有 dirty 审计和 review 文件不覆盖、不拼接、不纳入实现提交；审计单根仍为主 checkout。
- 原始 prompts/responses、私有轨迹、凭据、大日志和 checkpoint 不公开。
- 执行采用 Subagent-driven；每个任务实现后由新 reviewer 检查，Critical/Important 关闭后才进入下一任务。
- 本地 commit 用作任务边界和追踪，不强推、不修改上游 GitHub；GitLab 备份沿用用户既有授权。

## Review Focus

- 错误 rounds/quotas/allocated_tasks 可能把重复 scope 变成隐式多域或无限重试；Task 1 的参数化拒绝测试必须覆盖。
- 重复 ID 与 SQLite 主键冲突可能诱导伪造 disjoint release；Task 2 必须实测同一索引下五份 manifest 和 RoundStore B1/B5 可读。
- retain 后 `retry_pending`、attempt 计数和下一阶段可能相互矛盾；Task 3 必须用真实 `ExperimentProgress` 验证 B1 retain→B2、B5 finish。
- MODEL 导入可能把上一轮 candidate/skills 当作训练输入，或修改源 run；Task 4 必须验证输入只来自 A0/parent，并在 smoke 前后复算源哈希。
- 零调用过程异常可能制造表面增益；Task 6 的汇总器必须逐轮关联 new success/new regression 与 EAGAIN/ENOENT，不能只输出 delta。

---

### Task 1: 五轮 scope、重复 schedule 与配置校验

**Files:**
- Modify: `RSIFlow/runtime/rsiflow/task_meta/pipeline.py`
- Modify: `RSIFlow/task_adapter.py`
- Modify: `RSIFlow/evolution_protocol.py`
- Test: `RSIFlow/tests/test_deepcoder_five_round.py`

**Interfaces:**
- Produces: `PipelineConfig.experiment_scope="deepcoder_five_round_canary"`、`training_schedule="round_repeated"`；TaskAdapter 保留 launcher 中该 schedule，并把 `unique_tasks` 写入 `round_protocol`。
- Consumes: 既有 `training_quotas()`、`active_domains()`、`RoundStore`。

- [ ] **Step 1: 写配置与 adapter 失败测试**

```python
from test_deepcoder_canary import _canary_launcher_value

def _five_round_launcher_value(tmp_path):
    value = _canary_launcher_value(tmp_path)
    value.update(experiment_scope="deepcoder_five_round_canary",
                 training_schedule="round_repeated", rounds=5,
                 allocated_tasks=80, unique_tasks=16,
                 retry_rejected_from_round=6, evaluation_schedule="none")
    return value

def test_five_round_adapter_preserves_repeated_schedule(tmp_path):
    value = _five_round_launcher_value(tmp_path)
    path = tmp_path / "launcher.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    config = TaskAdapter(ROOT, tmp_path / "receipts")._config({"config_path": str(path)})
    assert config.experiment_scope == "deepcoder_five_round_canary"
    assert config.training_schedule == "round_repeated"
    assert config.max_generations == 5
    assert config.window_quotas == {"tool_use": 0, "code": 16, "searchqa": 0}
    assert config.round_protocol["unique_tasks"] == 16
    assert active_domains(config) == ("code",)

@pytest.mark.parametrize("override", [
    {"rounds": 4}, {"allocated_tasks": 16}, {"unique_tasks": 15},
    {"training_tasks_per_pass": 15},
    {"train_quotas_per_round": {"envscaler": 1, "deepcoder_taco": 16,
                                "hotpotqa": 0, "2wiki": 0}},
])
def test_five_round_rejects_inconsistent_scope(tmp_path, override):
    value = _five_round_launcher_value(tmp_path)
    value.update(override)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        TaskAdapter(ROOT, tmp_path / "receipts")._config({"config_path": str(path)})
```

同时保留 `test_deepcoder_canary.py` 的单轮 rounds=2 拒绝用例和多域正配额用例。

- [ ] **Step 2: 运行 RED**

Run: `PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests/test_deepcoder_five_round.py`
Expected: 新 scope 触发 Pydantic `Literal` 校验失败，测试是目标原因 RED 而非 fixture/import error。

- [ ] **Step 3: 实现最小配置扩展**

在 `PipelineConfig` 的 `Literal` 中加入新 scope/schedule；active-domain 和零配额分支使用两个 DeepCoder scope 的显式集合。将 full-cohort/no-search-dev 的一致性判断扩展到 `round_repeated`。TaskAdapter 仅对新 scope 读取 launcher schedule，其余入口继续强制 `round_disjoint`：

```python
deepcoder_scopes = {"deepcoder_canary", "deepcoder_five_round_canary"}
if value.get("experiment_scope") in deepcoder_scopes:
    source_quotas = dict(value.get("train_quotas_per_round", {}))
    training_quotas({**value, "train_quotas_per_round": {
        name: count for name, count in source_quotas.items() if count}})
# base.update(...) 中：
training_schedule=(value.get("training_schedule")
                   if value.get("experiment_scope") == "deepcoder_five_round_canary"
                   else "round_disjoint")
```

新 scope 额外要求 rounds=5、unique_tasks=16、allocated_tasks=80；`round_protocol` 增加 `training_schedule` 和 `unique_tasks`。既有错误消息和默认行为不改变。

- [ ] **Step 4: 运行 scoped 与回归测试**

Run: `PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests/test_deepcoder_five_round.py tests/test_deepcoder_canary.py tests/test_600_744_protocol.py`
Expected: 全 PASS；原单轮和多域测试未被修改来掩盖失败。

- [ ] **Step 5: 独立审阅并提交**

审阅 scope 集合是否所有分支一致、是否有隐式 schedule 放宽。修复 Critical/Important 后仅 stage 本任务文件：

```bash
git add RSIFlow/runtime/rsiflow/task_meta/pipeline.py RSIFlow/task_adapter.py \
  RSIFlow/evolution_protocol.py RSIFlow/tests/test_deepcoder_five_round.py
git commit -m "feat: add repeated DeepCoder five-round scope"
```

### Task 2: 固定重复 16 条的五份 frozen manifests 与机器配置

**Files:**
- Modify: `reproduction/scripts/prepare_deepcoder_canary.py`
- Create: `reproduction/scripts/prepare_deepcoder_five_round.py`
- Create: `reproduction/configs/train_4b_deepcoder_five_round_workspace.json`
- Test: `RSIFlow/tests/test_deepcoder_five_round.py`

**Interfaces:**
- Consumes: Task 1 新 scope；既有 `select_deepcoder_function()`、`repack()`、`freeze()`、`make_manifest()`。
- Produces: `build_five_round_release(output_root, machine_config_path, parent_manifest_path) -> dict`；release 包含 B1–B5 manifests、单一 `tasks.sqlite`、READY 与 selection。

- [ ] **Step 1: 写 release 失败测试**

构造 16 条带独立 record bytes 的 fixture，运行 builder 后：

```python
assert receipt["rounds"] == 5
assert receipt["unique_tasks"] == 16
assert receipt["allocated_task_slots"] == 80
assert receipt["schedule"] == "round_repeated"
ids_by_round = []
for number in range(1, 6):
    current = read(release / f"B{number}/manifest.json")
    assert current["round_id"] == number
    ids_by_round.append([row["task_id"] for row in current["tasks"]])
    assert all((release / f"B{number}" / "deepcoder_taco.jsonl").is_file()
               for _ in [0])
assert ids_by_round == [ids_by_round[0]] * 5
store = RoundStore(release / "tasks.sqlite")
for number in (1, 5):
    store.select_round(number)
    tasks, _ = store.window(None, {"code": 16})
    assert len(tasks) == 16
```

另测任一源 record 改动、既有输出目录、非 function、少于 16 条均 fail closed；builder 前后源资产 hashes 不变。

- [ ] **Step 2: 运行 RED**

Run: `PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" python -m pytest -q tests/test_deepcoder_five_round.py -k release`
Expected: 新 builder 不存在或 rounds 参数不支持，属于目标 RED。

- [ ] **Step 3: 实现 release builder**

复用单轮 builder 生成 B1 和唯一 SQLite；B2–B5 只对 selection 中 16 条调用 `repack()` 复制原 record bytes，再以 `make_manifest("train_evolution", number, tasks)` 和 `freeze()` 写 manifest。不要把重复 ID 再插入 SQLite。写 `protocol.json` 的 rounds=5，RoundStore 直接从每轮 manifest 读任务，不要求 SQL 中重复主键。

READY 明确写入：

```python
{
    "status": "ready", "schedule": "round_repeated", "rounds": 5,
    "task_count": 16, "unique_tasks": 16, "allocated_task_slots": 80,
    "round_manifest_hashes": {str(n): manifests[n]["manifest_hash"] for n in range(1, 6)},
    "source_assets_unchanged": True, "formal_round_completed": False,
    "model_calls": 0, "api_calls": 0, "gpu_calls": 0,
}
```

机器配置从单轮配置复制，精确修改 scope/schedule/rounds/allocated_tasks/unique_tasks/output_root/run_name/frozen_data_dir/data_release/retry=6；其余 Qwen、Astra、ports、SFT preset 输入保持不变。output/run 位于五轮 worktree 内。

- [ ] **Step 4: 构建真实 frozen release 并验证**

Run:

```bash
python reproduction/scripts/prepare_deepcoder_five_round.py \
  --output-root reproduction/logs/deepcoder-five-round-16-v1 \
  --machine-config reproduction/configs/train_4b_deepcoder_five_round_workspace.json \
  --parent-manifest RSIFlow/runtime/data/rounds_3000_5x600_v5/B1/manifest.json
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" python RSIFlow_4B/launch.py \
  --config "$PWD/reproduction/configs/train_4b_deepcoder_five_round_workspace.json" --check
```

Expected: READY 5×16/16 unique、源 hashes 不变；`--check` 显示 rounds=5、16/round、空 skill seed、新 output/run。

- [ ] **Step 5: 独立审阅并提交**

重点审查单一 SQLite 是否仅为索引载体、B1/B5 payload 路径是否严格位于当前 B_r、原始 release 是否未改。通过后提交 builder/config/tests 与 READY/selection/manifest 中适合 tracked 的小型冻结元数据，不提交大日志。

### Task 3: 五轮进度、prompt、retain 推进与恢复语义

**Files:**
- Modify: `RSIFlow/experiment_progress.py`
- Modify: `RSIFlow/launch_meta.py`
- Modify: `RSIFlow/persistent_meta.py`
- Test: `RSIFlow/tests/test_deepcoder_five_round.py`
- Test: `RSIFlow/tests/test_experiment_progress.py`

**Interfaces:**
- Produces: `_five_round_canary(policy) -> bool`；no-validation 进度支持 B1–B5，retain 为当前轮终态；所有 prompt/resume 文案一致。
- Consumes: 既有 milestone/turn receipt 重建逻辑，不重写控制器选择策略。

- [ ] **Step 1: 写五轮真实进度器测试**

用临时 turn receipts 驱动 A0 和五轮，B1 accept、B2 retain，其余轮可 accept；每轮写唯一 attempt_1 decision/selection 和 snapshot：

```python
state = progress.rebuild()
assert [s["id"] for s in state["stages"]] == ["A0", "B1", "B2", "B3", "B4", "B5"]
# 在 B1 完成后、B2 未开始前：
assert state["current_stage"] == "B2"
assert state["stages"][1]["attempt"] == 1
assert state["finish_allowed"] is False
# 仅完整五轮后：
assert state["current_stage"] == "COMPLETE"
assert state["finish_allowed"] is True
```

另测 B2 retain 不产生 B2 attempt_2、B1 snapshot 不算 B2 snapshot、缺失 B5 selection 时 finish=false、旧单轮 retain 仍终止整个一轮 run。

- [ ] **Step 2: 写指令一致性 RED**

对 `experiment_prompt()`、`persistent_instructions()`、`mainline_reminder()` 和 resume 中实际拼接的 instructions 断言：包含“one attempt per round”“retain completes the current round and advances”“B1-B5”“no independent validation”；不包含“sole attempt”“no next round”“fresh round-disjoint”“no fixed retry limit”。B5 前 finish 仍由工具 gate 拒绝。

- [ ] **Step 3: 实现 scope-specific 策略**

`validate_workflow_policy()` 接受两种且仅两种 no-validation 形态：旧 scope rounds=1/retry>1；新 scope rounds=5/retry=6。`no_validation` 覆盖两个 scope，但 stage purpose/mainline goal 分开。新 scope 使用既有 `retry_from=6` 防止 B1–B5 decision reset；selection retain 留下的 retry 标记在文案和 compact block 中解释为本轮已终止，不鼓励新 attempt。

在 `persistent_meta.py` 新增：

```python
def _five_round_canary(policy):
    policy = policy or {}
    return (policy.get("evaluation_schedule") == "none"
            and policy.get("experiment_scope") == "deepcoder_five_round_canary"
            and policy.get("rounds") == 5
            and policy.get("retry_rejected_from_round") == 6)
```

替换规则必须生成完整五轮指令，而不是先套用单轮替换。`launch_meta.experiment_prompt()` 同步描述 repeated batch、retain 后下一轮和 B5 finish。

- [ ] **Step 4: 运行进度、prompt、恢复回归**

Run: `PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests/test_deepcoder_five_round.py tests/test_deepcoder_canary.py tests/test_experiment_progress.py tests/test_launch_meta.py tests/test_persistent_meta.py tests/test_persistent_meta_sandbox.py`
Expected: 全 PASS；同 thread/job 恢复测试仍证明不重复 bootstrap/rollout。

- [ ] **Step 5: 独立审阅并提交**

审阅实际 resume prompt 路径，不只审 helper 输出；确认 `finish_experiment` 使用真实进度 gate。关闭发现后提交本任务文件。

### Task 4: 可重复多轮预检与 MODEL 导入 smoke 工具

**Files:**
- Create: `reproduction/scripts/deepcoder_five_round_preflight.py`
- Create: `reproduction/scripts/deepcoder_model_smoke.py`
- Test: `RSIFlow/tests/test_deepcoder_five_round.py`
- Test: `RSIFlow/tests/test_training_side_smoke.py`

**Interfaces:**
- Produces: `deepcoder_five_round_preflight.py --config ... --release ... --output ...` 写离线 preflight receipt。
- Produces: `deepcoder_model_smoke.py plan --source-run ... --output-root ... --machine-config ...` 和 `execute --output-root ...`；execute 包含 SFT/merge/reload/short generation/recovery/cleanup。
- Consumes: `training_side_smoke.derive_smoke_config()`、`_validate_sft_evidence()`、`_short_generation()`、owned-service helpers、TaskAdapter.sft。

- [ ] **Step 1: 写 preflight 和 source-import 测试**

Preflight 测试真实 config/release 加临时五轮回执，输出必须含 rounds=5、B1 accept/B2 retain 推进、B5 finish、无 validation、同 thread resume 指令和 `real_evolution_started=false`。

MODEL plan 测试构造只读 source-run fixture，验证：

```python
receipt = plan_model_smoke(source_run, output_root, machine_config)
assert receipt["source"]["state_path"].endswith("snapshots/A0/task/task_state_snapshot.json")
assert receipt["source"]["parent_dir"].endswith("round_1/parent")
assert receipt["source"]["accepted_candidate_used"] is False
assert receipt["source"]["skills_used"] is False
assert receipt["positive_parent_count"] > 0
assert receipt["status"] == "planned"
```

篡改 trajectory、A0 snapshot、performance、source state hash 或将 source parent 指向 candidate 时均拒绝；execute 在最终 plan 不存在、binding 改变或前一阶段 started 无 final receipt 时返回 requires_audit，不重训。

- [ ] **Step 2: 运行 RED**

Run: `PYTHONPATH="$PWD/runtime:$PWD:$PWD/../reproduction/scripts" python -m pytest -q tests/test_deepcoder_five_round.py tests/test_training_side_smoke.py -k 'five_round or model_smoke'`
Expected: 两个 CLI/函数尚不存在的目标 RED。

- [ ] **Step 3: 实现离线 preflight receipt**

Preflight 不重跑 pytest 代替自身检查：重新读取 config、五份 manifests，构造隔离临时 receipts 并调用生产 `ExperimentProgress`，检查每轮推进和 finish；核验三层指令。输出 reviewed commit、config/release hashes、检查项和明确的 `real_evolution_started=false`。

- [ ] **Step 4: 实现只读 MODEL 导入 plan/execute**

Plan 要求 source `state.status=complete`，读取 A0 snapshot receipt 中 `restorable_task_state_path` 和 `round_1/parent`，逐项验证 16 IDs、performance manifest、official success、真实 transport calls、A0 Task binding。用 `derive_smoke_config()` 创建新 output-root/registry，绝不复用 source output registry。封存所有 source bytes hashes，绝不复制或修改 source active candidate/skills。

Execute 重新验证 source seals 和 plan identity，使用新 registry 启动 base 四服务，然后调用生产 `TaskAdapter.sft()`，以 `_validate_sft_evidence()` 检查四 rank/one epoch/merged child/weights/Harness/artifacts，再 `_short_generation()`；随后在不重训的情况下再次 ensure/verify child 和短生成。finally 调和 training/services，源 run 前后 hashes 必须一致。每阶段原子写 final receipt，started 未封口禁止自动重跑。

- [ ] **Step 5: 运行 scoped 测试、审阅并提交**

Run: `PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests/test_deepcoder_five_round.py tests/test_training_side_smoke.py`
Expected: PASS；reviewer 重点检查 source immutability、registry 隔离、恢复时不重训和凭据不落地。通过后提交。

### Task 5: 完整离线门与 dev4 真实 MODEL smoke

**Files:**
- Create: `reproduction/audit/reviews/deepcoder-five-round-preflight-report.md`
- Create: `reproduction/audit/reviews/deepcoder-model-smoke-report.md`
- Modify: `reproduction/audit/reviews/deepcoder-five-round-sop.md`
- Create: `.superpowers/sdd/2026-11-18-deepcoder-five-round/progress.md`

**Interfaces:**
- Consumes: Tasks 1–4 reviewed HEAD、新 config/release、两个 preflight CLI。
- Produces: 可启动五轮的 reviewed HEAD 和真实 MODEL gate，或精确 blocker。

- [ ] **Step 1: 运行完整离线套件与静态检查**

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests
cd ..
python3 -m unittest discover -s web/tests -v
node --check web/app.js
node --check web/process.js
python3 reproduction/scripts/deepcoder_five_round_preflight.py \
  --config reproduction/configs/train_4b_deepcoder_five_round_workspace.json \
  --release reproduction/logs/deepcoder-five-round-16-v1/release \
  --output reproduction/logs/deepcoder-five-round-preflight-offline.json
```

Expected: 记录实际 pass/skip，不把 skip 算 pass；preflight receipt 全项通过。

- [ ] **Step 2: dev4 只读资源/API/bwrap 门**

使用 `dev-four-a800-access.md` 当前 SSH 入口；若动态 TCP 失效，保留 blocker 并通知用户。只 source 私有 env、检查 key 存在，不打印。核验 reviewed HEAD、四卡、端口、磁盘、bwrap hash/receipt、Responses 200 completed 和 run 路径不存在。任何失败不进入 SFT。

- [ ] **Step 3: 执行 MODEL plan 和真实 execute**

```bash
python reproduction/scripts/deepcoder_model_smoke.py plan \
  --source-run /data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/Rollout_logs/deepcoder_canary_16_v1/runs/deepcoder_function_16_canary_workspace_v1 \
  --output-root "$PWD/Rollout_logs/deepcoder_model_smoke_v1" \
  --machine-config "$PWD/reproduction/configs/train_4b_deepcoder_canary_workspace.json"
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
  python reproduction/scripts/deepcoder_model_smoke.py execute \
  --output-root "$PWD/Rollout_logs/deepcoder_model_smoke_v1"
```

Expected: natural positives>0、world_size=4、one epoch、merged child、四卡 child reload、短生成、recovery retrained=false、services stopped、source unchanged。失败则写实际 blocker，不启动五轮，不切换 fake/host backend。

- [ ] **Step 4: 独立真实证据审阅**

Reviewer 直接读取 training metrics、rank records、SFT selection、checkpoint manifests、service registry、短生成、source seal 和 cleanup，不只读 implementer 报告。所有 Critical/Important 关闭后更新 SOP 的可执行命令与当前 reviewed HEAD 绑定。

- [ ] **Step 5: 记录审计并提交报告**

使用主审计根、唯一 phase 和脱敏 evidence paths 记录真实操作与关键结果；保留既有 dirty 行。提交报告、SOP 和 SDD ledger，不提交私有 runtime 产物。

### Task 6: 启动并监控真实 A0+B1–B5，逐轮汇总

**Files:**
- Create: `reproduction/scripts/summarize_deepcoder_five_round.py`
- Test: `RSIFlow/tests/test_deepcoder_five_round.py`
- Create: `reproduction/audit/reviews/deepcoder-five-round-real-results.md`
- Modify: `.superpowers/sdd/2026-11-18-deepcoder-five-round/progress.md`

**Interfaces:**
- Consumes: Task 5 GO evidence、最终 detached runbook。
- Produces: `summarize(run_dir) -> dict` 逐轮真实 calls/verifier/transitions/errors/selection/active identity，以及真实完成轮数和 blocker。

- [ ] **Step 1: 先写结果汇总器测试**

构造两轮 fixture：一轮正增益但有 new success/regression 与零调用重合，一轮 retain。断言每轮分母=16、calls 从持久回执复算、full verifier 与 verification completed 分开、active identity 与选择一致；缺 B3 时 `completed_rounds=2` 且 `run_complete=false`。

- [ ] **Step 2: 实现汇总器并运行完整测试**

只读真实 run 文件，不改评分/selection。异常按 `execution_error_type` 和 `model_call_count==0` 统计，保留协议 `infrastructure_error` 原值。输出逐轮 JSON/Markdown 所需事实和证据 hashes。

- [ ] **Step 3: 启动 detached 五轮**

按 SOP 创建全新 output/run 和 supervision 0001，使用 reviewed HEAD/config、空 skills、同一 bwrap backend；不加 `--prepare-data` 或 launcher `--detach`。记录 PID/PGID/start identity。启动本身不记 PASS。

- [ ] **Step 4: 持续只读监控并在每轮封口后汇总**

使用 `monitor_meta.py`、workflow/receipts/jobs/registry 和汇总器。B1–B4 每轮 terminal 后核对 ID/hash、一个 attempt、组件、比较、active 和 snapshot 再允许继续观察；controller 不干预 Astra 合法选择。provider 502 或进程异常按 SOP 停止/调和/同 thread 新 supervision resume，不重复完成工作。

- [ ] **Step 5: 最终 cleanup 和真实结果审阅**

只有 A0+B1–B5、finish、jobs、app-server、registry、GPU 全部核验后才写 run complete。独立 reviewer 复算逐轮 calls、分数、transitions、MODEL/SFT 实际发生轮次和零调用限制。若中断则报告实际完成轮次，不宣称五轮。

### Task 7: 全分支终审、合并备份与公开交付

**Files:**
- Modify: `AGENTS.md`
- Modify: `reproduction/PROGRESS.md`
- Modify: `reproduction/REPRODUCTION.md`
- Modify: `reproduction/RETROSPECTIVE.md`
- Modify: `reproduction/LEARNING.md`
- Modify: `web/data/reproduction.json`
- Modify: `web/RESULTS.md`
- Modify: `web/OPERATIONS.md`
- Modify: `web/tests/test_web.py`
- Modify: `.superpowers/sdd/2026-11-18-deepcoder-five-round/progress.md`

**Interfaces:**
- Consumes: Task 6 真实审阅结论及所有证据 hashes。
- Produces: 一致、脱敏、可追踪的五轮交付；若未完成则公开实际 blocker 和完成轮数。

- [ ] **Step 1: 更新文档和公开 JSON**

准确写明固定 16 条重复五轮、80 positions/16 unique、每轮组件和 accept/retain、真实 SFT 轮次、零调用限制、无 held-out/694/744/作者复现。保留单轮历史报告，不重写旧结果。

- [ ] **Step 2: 更新网页契约并运行完整交付验证**

```bash
cd RSIFlow && PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests && cd ..
python3 -m unittest discover -s web/tests -v
node --check web/app.js && node --check web/process.js
python3 - <<'PY'
import json
import subprocess
from pathlib import Path
files = [Path(name) for name in subprocess.check_output(
    ['git', 'ls-files', '*.json'], text=True).splitlines()]
for path in files:
    json.loads(path.read_text(encoding='utf-8'))
print(f'parsed={len(files)}')
PY
```

进行 tracked diff/secret scan，确认无私钥、API key、原始轨迹或 runtime 日志进入 web。

- [ ] **Step 3: 全分支独立 Spec/质量审阅**

Reviewer 对照用户要求、spec、计划和原始真实证据；Critical/Important 必须修复并 scoped re-review。不能以离线 GREEN 替代缺失真实轮次。

- [ ] **Step 4: 合并与 GitLab 备份**

仅 stage 本计划文件，保留主 checkout 既有 dirty 文件。将审阅后的分支 fast-forward 或明确 merge 到 `reproduction/4b-main8`，再按既有非强推策略备份 GitLab main；记录源/目标 commit 和 merge 后测试结果。

- [ ] **Step 5: 实测公网并完成 SDD ledger**

复用当前 HTTP/cpolar 任务，不启动重复服务；实测首页、RESULTS、OPERATIONS、process 和 reproduction.json HTTP 200 及关键内容。若动态 URL 失效，更新实测地址并提醒用户。ledger 记录每任务 commit、测试归属、review verdict、真实 run/attempt、最终限制和未运行范围。
