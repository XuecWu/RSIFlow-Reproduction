# 真实 Qwen 最小评分 smoke 实现报告（Qwen implementer）

状态：**实现完成，离线 GREEN，真实资产 `--plan` 通过；真实模型执行未运行（按 brief 由 controller 审阅后在授权 GPU 作业中执行）。**

- 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`；分支 `reproduction/4b-preflight`
- 依据：`reproduction/audit/reviews/model-scoring-smoke-brief.md` 与 `model-scoring-smoke-design.md`
- 本次**未** commit/push，**未**启动模型/GPU/SCO/API/五轮/SFT，**未**读取凭据，**未**修改 `validate.py`、`tool_validation.py`、正式 manifest/config、JSONL 账本或其他生产代码，**未**修改主工作树或其他 worktree，**未**做任何 git trust/全局配置修改。

## 1. 交付文件（严格限定在 brief 允许范围）

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `reproduction/scripts/model_scoring_smoke.py` | 新建 | 独立 smoke runner，组合现有接口，不调用 `validate()` |
| `RSIFlow/tests/test_model_scoring_smoke.py` | 新建 | 29 个离线回归测试（temp dirs + monkeypatch，无 GPU/API/官方进程） |
| `reproduction/REPRODUCTION.md` | 修改 | 新增 §8：待执行的 plan/execute 命令、固定范围与协议边界 |
| `reproduction/audit/reviews/model-scoring-smoke-report.md` | 新建 | 本报告 |

运行产物（Git 忽略，`**/logs/`，`.gitignore:28`）：`reproduction/logs/model-scoring-smoke-plan-r1`（首次失败尝试，保留）、`-r2`（第二次失败尝试，保留）、`-r3`（成功的真实资产 plan）。失败尝试目录**未删除**，作为排障证据保留。

## 2. 设计与协议边界落实

- **默认 `--plan`**：只做资产/配置/确定性子集/binding 校验，不启动服务、不调用模型。真实执行必须显式 `--execute` 且 output root 不存在，并强制 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`，否则 fail closed。
- **确定性选择**（`select_subset`，规则在任何模型输出前声明）：HumanEval+/MBPP+/HotpotQA-dev/2Wiki-dev 各取父 manifest 原顺序第 1 个完整 task；BFCL 取原顺序前 50 个其 native IDs 全部存在于 pinned official `native_layout` 的完整 task。不足 50、重复 `task_id`、空/重复/不一致 `native_ids`、缺失 `native_id` 一律 `SmokeBlocked`。
- **父 manifest 缺口如实记录**：真实资产中 BFCL 152 条里 **133 条被官方识别、19 条未被识别**，19 条以 `bfcl_unrecognized`（含 `missing_native_ids`）写入子集与回执，标注为父 manifest 协议缺口；不静默修复、不宣称完整 694。
- **绑定与哈希**：保留原 manifest hash、子集文件 hash、选择规则、有序 task/native IDs、record offset/length/pack hash/content hash、spec/config/snapshot hash。`read_task_record` 校验 pack `record_sha256`、按 offset/length seek 读取，并用仓库自身语义 `data_protocol.text_hash(public_text(...))` 校验 `content_hash`（不是 raw-byte sha256）。
- **smoke-only 初始 Task 状态**：`freeze_smoke_task` 写 `smoke_task_state.json`（generation 0、Qwen3-4B、seed harness、真实 checkpoint 与 weights manifest）与 `source_round.json`（`smoke_only=true`、`rounds_completed=0`），再构造与 `validate.py` 同字段的 `frozen_task.json`（`status=frozen_for_report_eval`、`source_role=independent_validation`、`purpose=report_only`、`explicit_full_benchmark=false`、`manifest_hash` 绑定原 manifest、另记 `smoke_subset_hash`）。**不虚构已完成轮次或正式 snapshot。**
- **真实执行路径**：`TaskAdapter._config()` 取机器配置 → 四个不同 endpoint（GPU 0–3）→ `start_services`/`verify_services`（GPU/checkpoint/weights manifest 绑定）→ Code/QA 走 `generate_dynamic` + `evaluate_official` + `official_task_scores`；BFCL 走 `evaluate_tool`（`selected_tasks`/`selected_task_ids`，严格 50）；ACE 不执行、不创建 user client。
- **证据分类**：每任务必须唯一、非空字符串预测、`state_hash` 匹配、`split=report_eval`、`final_submission_count=1`、`record_hash` 存在、至少一次 `status=completed` 的本地模型调用，且无 `failed/requires_audit`。模型答错 → 官方 FAIL（可 scored）；空预测/缺 ID/重复 ID/异常/分母不符/未完成 RPC/服务或隔离失败 → `blocked`/`incomplete`，**绝不记模型零分**。
- **BFCL 后验**：要求官方 `task_results` 精确覆盖全部 selected native IDs、逐条 `execution_status`/`scoring_status` 均 completed、`expected==completed==len(ids)`、分类分母求和一致；并断言输出目录内**不存在** `user_*.receipt`。证据不足即 blocked，不靠 result `completed` 推断成功。
- **cleanup**：`try/finally` 只停止本 runner 拥有的 registry（`config.output_root` 被设为 smoke root，故 `registry_path(config)` = `<root>/services/four_gpu_services.json`）；`stop_services` 强制四卡各 ≥40,000 MiB；cleanup 失败单独记 `cleanup.error`，不覆盖原异常、不虚报释放；无 registry 时记「nothing to stop」。SIGTERM/SIGINT 触发清理并在 finally 恢复原 handler。
- **输出边界**：不写 `complete.json`；`aggregate_score=None`；逐 benchmark 分母（1/1/1/1 与 BFCL 50 完整 task / 展开 native 分母）；`feedback_to_training_or_meta=false`、`eligible_for_sft=false`；`held_out_exposure` 明确记录消费正式 held-out 条目、`exposure_unknown`、`clean_independence_audited=false`、必须与正式评估隔离。父协议 744/694/50 只作为**声明**记录，不作为本次分母。

## 3. TDD 证据

### 3.1 RED（先写测试，实际运行）

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/RSIFlow
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/runtime:$PWD" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_model_scoring_smoke.py -p no:cacheprovider
```

输出：

```text
tests/test_model_scoring_smoke.py:22: in <module>
    import model_scoring_smoke as smoke
E   ModuleNotFoundError: No module named 'model_scoring_smoke'
ERROR tests/test_model_scoring_smoke.py
!!!! Interrupted: 1 error during collection !!!!
1 error in 0.50s
```

### 3.2 第一次 GREEN 尝试：11 failed / 17 passed（fixture 缺陷，已修）

实现后首跑 `11 failed, 17 passed in 3.68s`。逐项定位为**测试 fixture 缺陷**，不是实现语义错误：

1. `record["_id"]` 直接下标在 Code 记录上抛 `KeyError`（`or` 左侧先求值）→ 改 `record.get("_id")`。
2. 测试传入不存在的 `"unused.json"` 作为 pipeline config，而 binding 需要对其取 hash → `FileNotFoundError`；fixture 增加真实 `launcher.json` 并全部改用该路径。
3. `main` 测试的 fake `plan` 返回字符串而非回执 dict → `TypeError: string indices must be integers`；改为返回完整最小回执。

修正后 `1 failed, 27 passed`：剩余失败是 fake `effective_config` 未像生产那样把 `config.output_root` 设为 smoke root，导致 registry 路径断言不一致；改为镜像生产行为后消除。

### 3.3 真实资产 `--plan` 暴露的两个真实缺陷（已修，并补回归测试）

1. **`OfficialEvaluatorSpec() got multiple values for keyword argument 'benchmark'`**
   机器 `reproduction/configs/report-evaluators-v4.json` 的每个 spec **自带 `benchmark` 字段**，而 `dynamic_predictions.generate_dynamic` 以 `OfficialEvaluatorSpec(**spec, benchmark=identifier)` 构造 → 重复关键字。
   处置：runner 写出的子集 spec **剔除 `benchmark` 键**（保留其余全部官方身份字段），构造对象时用 `OfficialEvaluatorSpec(**{**spec, "benchmark": source})`。新增回归测试 `test_parent_spec_carrying_benchmark_key_is_normalized_for_dynamic_stage`。
   **附带发现（不在本次修改范围）**：`evaluation_manifest.select_specs()` 以 `{**original[name], ...}` 保留 `benchmark` 键，因此**正式 694 路径经 `generate_dynamic` 时可能触发同一 TypeError**。本次未改任何生产代码；建议 controller 另行立项核实与修复。
2. **`'PipelineConfig' object has no attribute 'retrieval_index'`**
   检索索引实际由 `config.data_dir/search.sqlite` + `search.sqlite.manifest.json` 解析（`pipeline.py:251-255`），launcher 的 `retrieval_index` 只在数据准备阶段使用。
   处置：`asset_checks` 改为检查 frozen `search.sqlite`、其 manifest 存在性，并**校验 manifest `sha256` 与实际索引文件哈希一致**。

### 3.4 GREEN（最终）

```text
# 新测试
29 passed in 1.49s

# 相关测试（test_model_scoring_smoke / test_task_adapter / test_600_744_protocol /
#            test_skip_ace_protocol / test_tool_validation_preflight / test_evalplus_isolated / test_service_startup）
66 passed, 2 skipped in 28.74s
```

### 3.5 完整 RSIFlow pytest（实际运行，失败如实列名）

```bash
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/runtime:$PWD" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests -p no:cacheprovider --tb=line
```

结果：**`1 failed, 327 passed, 2 skipped in 84.81s`**

唯一失败（准确名称）：

```text
FAILED tests/test_custom_provider.py::CustomProviderTests::test_backend_accepts_registered_custom_endpoint_offline
E   subprocess.CalledProcessError: Command '['git', '-C',
    '/data/wuxuecheng/Hierarchical_level/RSI-Flow/assets/codex/source', 'rev-parse', 'HEAD']'
    returned non-zero exit status 128.
E   rsiflow.task_meta.meta_backends.contracts.BackendUnavailable:
    BLOCKED_META_BACKEND_COMPATIBILITY: Cannot verify the pinned source checkout
    （runtime/rsiflow/task_meta/meta_backends/codex_openrouter.py:343）
```

归因：**跨 uid 执行环境**，与本次改动无关。本 shell 为 uid 0，而该 pinned checkout 属 uid 10045，git 报 `dubious ownership`。按用户明确指示**未修改任何 git trust/全局配置**去「修绿」；该失败需由属主用户或主 agent 在正常属主环境复跑确认。计数自洽：上一轮基线 299 passed（含该测试在 trust 例外下通过）− 1（本次无 trust 例外）+ 29（本次新增）= 327 passed。

`TMPDIR=/tmp` 为进程级环境变量，用于避开 `TMPDIR` 属主不匹配导致的 `tmp_path` OSError；未修改任何文件。

## 4. 真实资产 `--plan` 回执（离线，无模型/GPU/API）

命令：

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python reproduction/scripts/model_scoring_smoke.py \
  --output-root reproduction/logs/model-scoring-smoke-plan-r3 \
  --config "$PWD/reproduction/configs/train_4b.json" \
  --manifest /data/wuxuecheng/Hierarchical_level/RSI-Flow/RSIFlow/runtime/data/validation_744_v4/manifest.json \
  --settings "$PWD/reproduction/configs/validation_744.json"
```

stdout：

```json
{"bfcl_unrecognized_count": 19, "blockers": [], "execute_ready": true, "mode": "plan", "ok": true,
 "output_root": "reproduction/logs/model-scoring-smoke-plan-r3", "selected_tasks": 54, "status": "planned"}
```

回执要点（`reproduction/logs/model-scoring-smoke-plan-r3/plan_receipt.json`）：

- `scope = minimal_real_model_scoring_smoke_not_a_benchmark`；`mode=plan`、`status=planned`、`model_calls=0`、`execute_ready=true`、`blockers=[]`。
- 原 manifest hash `94d5e8af7eb0e32854116307299093360d96bd44fa37837c3315c1b080b42b7e`（与设计观察值一致，运行时重新校验）；父 task 数 744；协议声明 744 frozen / 694 scored / 50 pending。
- 子集 sha256 `879ce5de2fa5a0c37b31475545925087cf0c680fe68cf80be414d64c5569c62b`；选中 **54** 个完整 task。
- Code/QA 各 1：`humaneval_plus:HumanEval/0`、`mbpp_plus:Mbpp/100`、`hotpotqa_dev:5a72120e55429971e9dc923f`、`2wiki_dev:06326df2088611ebbd6dac1f6bf848b6`。
- BFCL 50：原顺序前 50 个被官方识别者，首三条 `bfcl_v3:irrelevance_104`、`irrelevance_151`、`irrelevance_160`，末条 `bfcl_v3:live_multiple_229-103-0`；`bfcl_recognized_total=133`、`bfcl_unrecognized_count=19`（含 `5021fea2…`、`64066c33…`、`exec_multiple_40` 等）。
- binding：`state_hash=a982732cf793662dda7a4bd80ce03492ef71f690d81d9c5034a11529be650ec1`，`manifest_hash` 绑定原 manifest，`smoke_subset_hash` 绑定子集，`purpose=report_only`、`source_role=independent_validation`、`explicit_full_benchmark=false`，`checkpoint_files=3`（真实 Qwen3-4B 分片）。
- 资产检查全 true：四不同 endpoint、checkpoint、frozen `search.sqlite` 与其 manifest 及**哈希一致**、isolation receipt 与 `bin/bwrap`、显式 bwrap backend、四个官方 entrypoint、BFCL repository。
- `ace = {executed:false, user_simulator:false, pending_tasks:50}`；`held_out_exposure` 记录消费正式 held-out、`exposure_unknown`、`clean_independence_audited=false`、必须与正式评估隔离。
- 子集目录含 4 组 data/ids 与 `smoke_subset.json`；**无 `complete.json`**。

**真实模型执行未运行**：本开发机 `nvidia-smi` 报 `Failed to initialize NVML: Unknown Error`，无可用 GPU；按 brief 不启动真实 GPU/SCO，执行由 controller 审阅后在授权作业中进行。

## 5. 测试覆盖对照 brief 要求

| brief 要求 | 覆盖测试 |
| --- | --- |
| 确定性选择 | `test_selection_is_deterministic_and_takes_first_complete_task_per_code_qa` |
| 50 完整 BFCL | `test_bfcl_selects_exactly_first_50_recognized_complete_tasks_in_parent_order` |
| 无效 ID/family fail closed | `test_bfcl_fewer_than_50_recognized_fails_closed`、`test_ambiguous_or_missing_native_identity_fails_closed`、`test_native_ids_family_must_be_complete_and_consistent` |
| 父 manifest 缺口记录 | `test_bfcl_unrecognized_ids_are_reported_as_parent_protocol_gap` |
| spec/hash/原 manifest binding | `test_record_read_verifies_pack_hash_offset_and_content_hash`、`test_subset_specs_preserve_official_identity_and_bind_parent_manifest`、`test_subset_spec_rejects_tampered_data`、`test_parent_spec_carrying_benchmark_key_is_normalized_for_dynamic_stage`、`test_binding_and_snapshot_are_smoke_only_and_bind_parent_manifest` |
| 空预测/错误 state hash/重复 ID | `test_empty_prediction_is_blocked_not_model_zero`、`test_wrong_state_hash_duplicate_id_and_missing_id_are_blocked`、`test_missing_completed_model_call_is_blocked`、`test_infrastructure_error_row_is_blocked`、`test_empty_model_answer_blocks_benchmark_and_keeps_other_evidence` |
| 模型错误答案可 scored | `test_wrong_model_answer_is_a_valid_scored_submission`、`test_official_wrong_answer_is_scored_not_blocked` |
| ACE 不触发 | `test_execute_never_triggers_ace_user_client` |
| 阶段顺序与 cleanup | `test_execute_stage_order_and_owned_service_cleanup`、`test_cleanup_failure_preserves_original_exception_and_is_reported`、`test_service_start_failure_still_attempts_owned_cleanup` |
| blocked 不变成模型零分 | `test_official_blocked_result_never_becomes_model_zero`、`test_bfcl_evidence_gap_is_blocked` |
| 显式执行/新目录/显式 bwrap | `test_execute_requires_explicit_flag_and_fresh_output_root`、`test_execute_without_explicit_bwrap_backend_fails_closed`、`test_execute_fails_closed_on_asset_blockers` |
| plan 不启动服务/模型 | `test_plan_never_starts_services_or_calls_the_model` |
| 不汇总为 694/744 | `test_execute_reports_per_benchmark_denominators_without_aggregate_score` |

所有 fixture/输出均在临时目录；GPU、官方进程、服务启停、`effective_config`、`official_native_ids`、`asset_checks` 均经 monkeypatch 隔离。**合成回执仅为 test-only fixture，不构成真实 smoke 证据**；真实隔离仍须由既有 bwrap/EvalPlus smoke 与后续真实执行证明。

## 6. 自审

- **范围合规**：只新建/修改 brief 允许的 4 个文件；未触碰 `validate.py`、`tool_validation.py`、正式 manifest/config、JSONL 账本、其他生产代码、主工作树或其他 worktree。mtime 核对显示 `validate.py`、`tool_validation.py`、`report-evaluators-v4.json`、`train_4b.json` 均为工作树创建时间（16:33）未被改动；`reproduction/configs/validation_744.json` 的 16:54 mtime 来自并行的 controller 活动（非本次写入），本次仅只读引用它。
- **最小实现**：不重写官方 comparator，不引入新依赖，只组合既有接口；对上游的两处不兼容采取**runner 侧适配**（剔除子集 spec 的 `benchmark` 键、按 `data_dir` 解析检索索引），未改上游代码。
- **fail closed**：选择不足/歧义、pack/content hash 不符、spec 身份不符、空预测、缺 ID、重复 ID、未完成/需审计调用、官方分母不符、BFCL 证据不足、资产 blocker、非显式 bwrap、output root 已存在 → 全部 blocked/incomplete，绝不降级为模型零分或静默通过。
- **只读正式数据**：父 manifest 与 record pack 只读；子集/spec/binding/报告全部写入 Git 忽略的新目录；两次失败尝试目录保留未删。
- **无完成标记**：不写 `complete.json`，不产生跨 benchmark 准确率，不把 54 项冒充 694/744；ACE 保持 pending。
- **暴露与隔离**：明确记录消费正式 held-out 条目的 exposure 与 `clean_independence_audited=false`，要求与后续正式评估隔离；若不接受该污染，应另行批准独立 smoke 数据（本次未擅自替换数据）。
- **安全与权限**：未读取/打印凭据；真实执行不需要 `OPENAI_API_KEY` 或 ACE 凭据；未做 git trust/全局配置修改；被跨 uid 阻断的检查如实记为环境性失败并交由属主复跑。
- **遗留/待办**：
  1. 真实 `--execute` 未运行（需授权 GPU 作业）；BFCL 50 的 category/family 兼容性、真实 evaluator 子集分母、四卡服务与 SCO 挂载仍待真实执行验证。
  2. `test_custom_provider` 的跨 uid 失败需属主环境复跑确认。
  3. 附带发现的正式路径 `select_specs` 保留 `benchmark` 键与 `generate_dynamic` 构造方式潜在冲突，建议 controller 另行立项核实（本次未改生产代码）。
  4. `git diff --check` 在本工作树因跨 uid 被阻断（`warning: Not a git repository`，exit 129），未绕过；需由属主用户/主 agent 补跑。

## 7. 复现命令汇总

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/RSIFlow

# 新测试 / 相关测试 / 完整套件
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/runtime:$PWD" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests/test_model_scoring_smoke.py -p no:cacheprovider
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/runtime:$PWD" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests -p no:cacheprovider --tb=line

# 真实资产离线 plan（新目录）
cd ..
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python reproduction/scripts/model_scoring_smoke.py \
  --output-root reproduction/logs/model-scoring-smoke-plan-new \
  --config "$PWD/reproduction/configs/train_4b.json" \
  --manifest /data/wuxuecheng/Hierarchical_level/RSI-Flow/RSIFlow/runtime/data/validation_744_v4/manifest.json \
  --settings "$PWD/reproduction/configs/validation_744.json"
```

## 8. Fix round 1（回应 `model-scoring-smoke-review.md`）

审阅结论为 Spec FAIL / 质量 CHANGES，两项 finding 均已处理；**未放宽任何审查要求**。

### 8.1 Important — BFCL 逐任务非空 submission 与可归属的已完成本地调用

**先写 RED 测试**（新增 18 个用例，含 12 个参数化负例），再最小实现。

RED 观察（分两阶段，均为实际运行）：

```text
# 阶段一：新增测试 + autouse PROJECT fixture（实现尚缺 PROJECT 与校验函数）
47 errors in 1.25s        # AttributeError: module 'model_scoring_smoke' has no attribute 'PROJECT'

# 阶段二：先只实现 Minor（output root 校验），保留 Important 未实现 → 行为性 RED
15 failed, 32 passed in 2.76s
# 其中 14 项为 BFCL 证据用例（valid 证据未被核验/未计调用数，12 个负例未被判 blocked，
# 以及 official completed 但无证据仍被当作 scored）；另 1 项为 fixture 的 mkdir 缺 parents=True
```

**实现**（`reproduction/scripts/model_scoring_smoke.py`）：

- `_native_submission(row, native_id)`：只在显式允许列表 `BFCL_SUBMISSION_FIELDS = ("result","response","output","model_response","function_calls")` 中取值，**要求恰好一个非空字段**；字符串按 `strip()` 判非空，list/dict 按非空结构判非空（兼容 BFCL 结构化调用输出）。无字段、全空、或多个非空字段 → `SmokeBlocked`，**不猜字段、不猜顺序**。
- `_bfcl_task_evidence(directory, binding, selected_ids, task_results)`：
  1. `task_results` 必须精确覆盖 selected native IDs、无重复，且每条带 `category` 与 `trajectory_path`；
  2. 每个 category 的预测文件必须**唯一**，文件内 `id` 集合与该 category 的 selected IDs **精确相等**、无重复；
  3. 逐 ID 核验唯一非空 native submission；
  4. 要求显式归属产物 `<output>/task_call_attribution.json`：绑定 frozen `state_hash`、`tasks` 键集合精确等于 selected IDs、每任务回执键列表非空且无重复、**同一回执键不得归属两个任务**；
  5. 逐回执核验 `transport/<key>.json` 存在、`status=completed`、`user_simulator is False`、`identity.state_hash` 匹配、`calls` 非空且**全部** `completed`；
  6. 返回 `verified_native_submissions`、`completed_model_calls`、`attributed_receipts`、`task_call_attribution_sha256`。
- `_score_bfcl` 在既有官方 `completed`/ID 覆盖/分母对账之后调用上述核验；任何失败 → 该 benchmark `blocked` 并保留精确 `error`，**不记模型零分、不记 scored**。
- **BFCL 调用数计入总数**：`stage["completed_model_calls"]` 现由 BFCL 填充，报告 `model_calls = sum(...)` 因此包含 BFCL。测试断言：有效证据时 `model_calls == 54`（Code/QA 4 + BFCL 50）；任一负例时 BFCL 计 0、总数为 4。

**关键事实与 fail-closed 结论（必须向 controller 明示）**：上游 `HarnessAPI.complete()` 以 `value_hash({state_hash, request, protocol, evaluation_scope})` 为回执键，`identity.request` 只含官方 runner 发来的 `messages/tools`，**不含 native task ID**；`official_tool_entry.py` 的 RPC 也只传 `{rpc_id, request}`；`evaluate_tool`/`metric_rows` 不产出任何逐任务归属文件（`metric_rows` 的 `tool_calls` 恒为 `None`）。因此**当前官方结构无法提供无歧义的逐任务调用归属**。

按 brief「若 BFCL 官方结构无法可靠映射，必须 fail closed 并在报告说明，不能放宽审查」，本 runner 的处理是：**只接受显式、无歧义的归属产物；缺失即 blocked**。这意味着在真实 `--execute` 中，若上游未提供该产物，BFCL 将被判 `blocked/incomplete` 而非 `scored`。关闭该缺口需由 controller 另行立项让上游产出可核验的逐任务归属；本次**未修改任何生产代码**，也**未**用「按预测顺序匹配回执」「按请求内容启发式匹配」等猜测手段伪造归属。

### 8.2 Minor — plan/execute 输出根目录必须是 PROJECT 内 Git 忽略的新目录

**先写 RED 测试**（4 个用例：PROJECT 外拒绝、可跟踪路径拒绝、git 权威判定 not-ignored 拒绝、校验方式入回执），再实现。

实现 `validate_output_root(root)`（plan 与 execute 入口均调用，且在任何配置/服务动作之前）：

1. `resolve()` 后必须严格位于 `PROJECT` 内且不等于 PROJECT；
2. 必须**尚不存在**；
3. 优先 `git -C PROJECT check-ignore -q --no-index <path>`：returncode 0 → 忽略；1 → **未忽略，拒绝**；其他（如跨 uid 的 128）视为 git 不可用；
4. git 不可用时退回**保守**的 `.gitignore` 祖先目录规则：只接受 `**/<name>/` 或顶层 `<name>/` 明确忽略的祖先目录，且**任何 negation 触及该子树即拒绝**（不假定忽略）；
5. 回执记录 `output_root_validation = {path, inside_project, preexisting, git_ignored, verification}`，`verification` 为 `git_check_ignore` 或 `structural_gitignore_ancestor`，便于审计区分权威判定与保守回退。

真实项目验证（git 因跨 uid 不可用，走保守回退）：

```text
# 可跟踪路径被拒绝
{"error": "Output root is not provably Git-ignored (git unavailable): …/reproduction/configs/should-be-rejected",
 "ok": false, "status": "blocked"}
```

### 8.3 修复后测试与真实资产 plan

```text
# focused（新测试文件）
47 passed in 2.29s

# 相关（test_model_scoring_smoke / test_task_adapter / test_600_744_protocol /
#       test_skip_ace_protocol / test_tool_validation_preflight / test_evalplus_isolated / test_service_startup）
85 passed, 2 skipped in 25.98s
```

真实资产 `--plan`（新目录 r5；r4 目录已被并行 controller 活动占用，本 runner 按「必须不存在」规则正确拒绝并改用 r5）：

```json
{"bfcl_unrecognized_count": 19, "blockers": [], "execute_ready": true, "mode": "plan", "ok": true,
 "output_root": ".../reproduction/logs/model-scoring-smoke-plan-r5", "selected_tasks": 54, "status": "planned"}
```

回执核对：`output_root_validation = {git_ignored: true, inside_project: true, preexisting: false, verification: "structural_gitignore_ancestor"}`；子集 sha256 `879ce5de2fa5a0c37b31475545925087cf0c680fe68cf80be414d64c5569c62b`、原 manifest hash `94d5e8af…b42b7e`、`state_hash a982732c…0ec1` 与修复前 r3 **完全一致**（选择与绑定确定性未受本轮修改影响）；54 选中、19 未识别、blockers 为空。

完整套件：按本轮分工由**主 agent**运行（修复前主 agent 已独立取得 328 passed / 2 skipped）；本 implementer 只跑 focused 与相关测试，不冒充全量结果。上一轮本 implementer 的全量结果为 `1 failed, 327 passed, 2 skipped`，唯一失败仍是跨 uid 的 `test_custom_provider.py::CustomProviderTests::test_backend_accepts_registered_custom_endpoint_offline`（git `dubious ownership`，exit 128），与本次改动无关，未以 git trust 修改「修绿」。

### 8.4 本轮修改文件与边界

- 修改：`reproduction/scripts/model_scoring_smoke.py`、`RSIFlow/tests/test_model_scoring_smoke.py`、`reproduction/REPRODUCTION.md`（§8 增补 BFCL 逐任务证据、上游归属限制与输出根目录校验）
- 追加：本报告 §8
- 未改 `validate.py`、`tool_validation.py`、`harness_api.py`、正式 manifest/config、JSONL 账本或其他生产代码；未 commit/push；未启动 GPU/SCO/API/模型；未读取凭据；未做 git trust/全局配置修改；未删除任何文件（r1/r2/r3 失败与历史 plan 目录均保留）。
- 测试仍全部使用 temp dirs + monkeypatch 隔离 GPU/官方进程；新增 BFCL 证据 fixture 明确标注 **test-only synthetic**，不构成真实 smoke 证据。

### 8.5 遗留（需 controller 决策）

1. **BFCL 逐任务调用归属产物**：上游当前不产出，真实执行将 fail closed 为 blocked。需另行立项让 `evaluate_tool`/`HarnessAPI` 输出可核验的 native-ID→回执映射（含多轮任务的多回执归属），否则 BFCL 无法在本 smoke 中取得 scored。
2. 真实 `--execute` 仍未运行（需授权 GPU 作业）；四卡服务、真实官方子集分母、真实隔离与 cleanup/显存释放仍待真实证据。
3. 跨 uid 导致的 `test_custom_provider` 失败与 `git diff --check` 阻断需属主用户/主 agent 复跑；本轮 git 不可用时的输出根校验为**保守回退**，在属主环境会自动使用 `git check-ignore` 权威判定。

## 9. Fix round 2（回应 scoped re-review 的新 Important）

re-review 结论：原 Important 与原 Minor 均 **ADDRESSED**；但 fix round 1 的 diff 新增一项 Important —— `task_call_attribution.json` 中的任务→回执映射**只验证文件自称的 state hash、ID 覆盖、回执状态与唯一归属**，未验证映射由可信生产方生成、也未验证回执请求确实对应所声称的 native ID，因此伪造或错误归属文件仍可能让不相关的已完成调用被计作该任务证据。本轮已修复，**未放宽审查**。

### 9.1 先写 RED（两阶段，均实际运行）

更新 fixtures 使「有效证据」包含可信绑定，并新增 12 个伪造/未绑定负例（负例总数 12 → 24）：

```text
# 阶段一：仅更新 fixtures + 新增负例（实现未改）
2 failed, 57 passed in 2.96s
# 失败：valid 证据用例（BFCL 仍 blocked、未计调用数）与 official-wrong-answer 用例
# 注意：此时 24 个负例虽全部 blocked，但是因旧实现拒绝新条目格式而通过，属“错误原因通过”

# 阶段二：把负例断言强化到具体拒绝原因（BFCL_BLOCK_REASONS 映射）后重跑 → 有意义的 RED
26 failed, 33 passed in 3.99s
# 负例现在因“拒绝原因不符”而失败，确保实现必须按可信绑定语义逐项拒绝
```

### 9.2 最小实现（`reproduction/scripts/model_scoring_smoke.py`）

新增常量 `TRUSTED_ATTRIBUTION_GENERATOR = "evaluate_tool_native_request_attribution_v1"`，并在 `_bfcl_task_evidence()` 中把归属核验升级为**可信链**：

1. **官方 result 绑定**：从磁盘读取该 benchmark 的 `result.json`，要求内含 `task_call_attribution = {generator, path, sha256}`；缺失 → `Official result does not bind a trusted task-call attribution`（**自称的映射文件本身不是证据**）。
2. **可信 generator（两处）**：result 绑定与归属文件都必须声明 `generator == evaluate_tool_native_request_attribution_v1`；手写/其他 generator → blocked。
3. **路径约束**：`path` 解析后必须位于该 benchmark 输出目录内，防止指向外部文件；文件必须存在。
4. **重算文件哈希**：`file_hash(attribution_path)` 必须等于 result 绑定的 `sha256`；**运行后被篡改/伪造** → `Attribution file hash does not match the official result binding`。
5. **归属内容绑定**：`state_hash` 必须等于 frozen smoke state；`selected_task_ids` 必须无重复且集合精确等于 runner 自己的 selected IDs；`selected_task_ids_sha256` 必须等于 `durable.value_hash(declared_ids)`（runner 重算）；`tasks` 键集合精确覆盖 selected IDs。
6. **每回执请求内容 hash**：条目必须是 `{receipt_key, request_sha256}`（64 位 hex），旧的纯字符串列表格式被拒绝；runner 读取 `transport/<key>.json`，要求 `identity.request` 为 dict，并**重算 `value_hash(identity.request)`** 与条目声明比对，不符 → `request-content hash mismatch`。**不再只信 JSON 中声称的 native ID。**
7. **回执状态与唯一归属**：`status=completed`、`user_simulator is False`、`identity.state_hash` 匹配、`calls` 非空且全部 completed；先做**全局唯一归属 pass**（同一回执键不得跨任务共享），再逐回执核验，避免顺序相关的误判。
8. 通过后返回 `verified_native_submissions`、`completed_model_calls`、`attributed_receipts`、`attribution_generator`、`task_call_attribution_sha256`；BFCL 调用数继续计入报告 `model_calls`。

请求内容 hash 采用仓库自身的 `rsiflow.task_meta.durable.value_hash`（`sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))`），与未来可信生产方使用同一规范化语义，避免自定义哈希造成不可复核。

### 9.3 负例覆盖（24 项，全部断言具体拒绝原因）

`missing_attribution`、`result_binding_missing`、`result_binding_wrong_generator`、`result_binding_external_path`、`result_binding_wrong_hash`、`forged_attribution_tampered`（绑定后改文件）、`untrusted_generator`、`attribution_missing_generator`、`attribution_wrong_state`、`attribution_wrong_selected_ids`、`selected_ids_hash_mismatch`、`attribution_missing_task`、`request_hash_missing`（旧字符串格式）、`request_hash_mismatch`、`receipt_request_missing`、`shared_receipt`、`receipt_missing`、`receipt_not_completed`、`call_not_completed`、`user_simulator_receipt`、`wrong_state_hash`、`empty_submission`、`ambiguous_submission`、`no_submission_field`。

每项均断言：`status == blocked`、`error` 含该模式对应的精确原因片段、`completed_model_calls == 0`、报告 `status == incomplete`、`model_calls == 4`（仅 Code/QA）。有效证据用例断言 `scored`、`completed_model_calls == 50`、`verified_native_submissions == 50`、`model_calls == 54`。

### 9.4 修复后测试与真实资产 plan

```text
# focused
59 passed in 2.90s

# 相关（test_model_scoring_smoke / test_task_adapter / test_600_744_protocol /
#       test_skip_ace_protocol / test_tool_validation_preflight / test_evalplus_isolated / test_service_startup）
97 passed, 2 skipped in 25.14s
```

真实资产 `--plan`（新目录 r6）：

```json
{"bfcl_unrecognized_count": 19, "blockers": [], "execute_ready": true, "mode": "plan", "ok": true,
 "output_root": ".../reproduction/logs/model-scoring-smoke-plan-r6", "selected_tasks": 54, "status": "planned"}
```

子集 sha256 `879ce5de2fa5a0c37b31475545925087cf0c680fe68cf80be414d64c5569c62b`、`state_hash a982732cf793662dda7a4bd80ce03492ef71f690d81d9c5034a11529be650ec1` 与 r3/r5 **完全一致**；`output_root_validation` 仍为 `git_ignored=true / inside_project=true / preexisting=false / verification=structural_gitignore_ancestor`（git 因跨 uid 不可用）。

完整套件：按分工由**主 agent**运行，其当前结果为 **346 passed / 2 skipped**；本 implementer 本轮只跑 focused 与相关测试，不冒充全量结果。

### 9.5 本轮修改文件与边界

- 修改：`reproduction/scripts/model_scoring_smoke.py`、`RSIFlow/tests/test_model_scoring_smoke.py`、`reproduction/REPRODUCTION.md`（§8 BFCL 证据条目升级为可信绑定描述）
- 追加：本报告 §9
- 未改 `validate.py`、`tool_validation.py`、`harness_api.py`、`dynamic_predictions.py`、正式 manifest/config、JSONL 账本或其他生产代码；未 commit/push；未启动 GPU/SCO/API/模型；未读取凭据；未做 git trust/全局配置修改；未删除文件（r1–r5 历史目录保留）。
- 所有 BFCL 证据 fixture 仍为 **test-only synthetic**，不构成真实 smoke 证据。

### 9.6 仍然 fail closed（重要）

**当前生产不产出该可信归属**：`evaluate_tool` 不写 `task_call_attribution.json`，也不在 `result.json` 中绑定它；`HarnessAPI` 回执不含 native task ID。因此真实 `--execute` 中 BFCL 仍会 **blocked/incomplete**，这是预期且正确的 fail-closed 行为，不是回归。关闭缺口需 controller 另行立项，让上游以 `evaluate_tool_native_request_attribution_v1` 产出归属、在官方 result 中绑定 path/SHA256/generator，并为每个回执绑定请求内容 hash（含多轮任务的多回执归属）。本轮**未**通过修改生产代码、手写归属文件或启发式匹配来让 BFCL 变绿。
