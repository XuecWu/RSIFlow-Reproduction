CHANGES

# Experiment Preflight 盘点（Qwen r1）

**结论：CHANGES** —— 把「五类 benchmark 最小 smoke」从模糊口径落实为当前代码中的具体五个 benchmark、最小输入、官方执行器、现有脚本、机器配置、预期回执与缺口。**五类中只有 2 类能用现有脚本按原样直接执行，2 类需要新增/参数化适配，1 类（BFCL）可执行但只是零推理注册检查；四卡 Qwen 健康 smoke 脚本齐备但本机无可用 GPU；最终 694 评估入口存在一个硬阻塞（`validation_sources` 依赖的两个 `data_path` 文件不存在）。** 因此 preflight 门不能关闭。

## 0. 任务边界与本次实际执行的动作

- 工作目录：主工作树 `/data/wuxuecheng/Hierarchical_level/RSI-Flow`；盘点时 HEAD `9f4c2624e1bc30f72f60272286a314fb297819f7`（`docs: 完成合并备份与公网交付记录`，`gitlab` 两个复现分支同指该 SHA）。
- 只读盘点。**未运行任何实验、未提交 SCO、未调用任何 API、未修改代码或配置、未读取或打印任何凭据**（只引用凭据环境变量名）。
- 本次唯一执行的「命令」是 `--check` 路径巡检（`launch_meta.py:507-529` 在 `run_dir.mkdir` 之前打印 facts 并 `return 0`，无写入、无 API），以及只读的文件存在性/哈希/计数核对与 `nvidia-smi` 查询。其余全部为静态代码与配置阅读。
- 只写本文件；未改动 `operations.jsonl`、`key_results.jsonl`、`Meta_logs/` 或任何生产代码/测试/配置。
- **历史 smoke 一律不当作当前通过**：见第 8 节的归属表。

## 1. 「五类 benchmark」的准确定义（694 口径）

不是「五个领域」，而是 **694 评分口径下的五个 benchmark**（第六个 ACEBench 50 条被 skip、标记 pending）：

| # | benchmark_id | 领域（`data_protocol.py:62`） | 冻结条数 | 官方执行器族 |
| --- | --- | --- | --- | --- |
| 1 | `bfcl_v3` | `tool_use` | 152 | BFCL 官方 native runner（pinned checkout） |
| 2 | `humaneval_plus` | `code` | 164 | EvalPlus 官方 `evalplus.evaluate` |
| 3 | `mbpp_plus` | `code` | 178 | EvalPlus 官方 `evalplus.evaluate` |
| 4 | `hotpotqa_dev` | `searchqa` | 100 | HotpotQA 官方 `hotpot_evaluate_v1.py` |
| 5 | `2wiki_dev` | `searchqa` | 100 | 2Wiki 官方 `2wikimultihop_evaluate.py`（original v1） |
| — | `acebench`（**不在五类内**） | `tool_use` | 50 | ACEBench 官方 native + 用户模拟 API |

证据：
- `reproduction/configs/validation_744.json`：`benchmark_ids` 六项、`validation_limits` = 152/50/164/178/100/100、`official_specs` 指向 `report-evaluators-v4.json`、`tool_benchmarks` 含 `bfcl_v3`/`acebench`。
- `RSIFlow/runtime/data/rounds_3000_5x600_v4/READY.json` → `evaluation_scope`：`frozen_validation_tasks=744`、`scored_validation_tasks=694`、`pending_validation_tasks=50`、`skip_acebench=true`、`status=partial_ace_pending_audit`；`status=prepared_not_executed`。
- `RSIFlow/validation_skip.py:10-19 evaluation_scope()` 与 `:22-45 ace_skip_summary()`：`expected = [name for name in benchmark_ids if name != "acebench"]`（正好 5 个），强制 `officially_scored == 694`、`expected_total == 744`、`metrics.overall.complete == False`、`scores/acebench/result.json` 不得存在，输出 `status=code_search_completed_ace_pending`、`acebench_status=incomplete_requires_audit`、`feedback_to_meta=False`。
- `reproduction/configs/train_4b.json` → `skip_acebench: true`、`run_name: rsiflow_skill_4b_600x5_eval694`、`evaluation_schedule: final_only`。
- 本次只读核对：`validation_744_v4/manifest.json` 744 条，逐源计数与上表完全一致，非 ACE 合计 **694**。

> 口径提醒：`web/data/reproduction.json` 的 stage id 仍写 `evaluate_4b_744`/`evaluate_8b_744`，而实际评分是 694；公开表述必须同时给出「744 冻结 / 694 评分 / 50 pending」。

## 2. 逐项盘点

### 2.1 BFCL-v3（tool_use，152）

- **最小输入（现有脚本口径）**：**零推理**。`RSIFlow/tool_validation.py:41 check_native()` 只把官方 runner 在隔离中 import/register，用 `fake_only` handler 拦截 SDK 调用，**恰好 2 次受控 CPU RPC**（`model == "gpt-rsi-preflight"`，返回固定 `test_override_cpu_only`），`run_native(..., timeout=900)`。不加载模型、不产生分数。
- **官方执行器**：`assets/evaluators/bfcl-official/berkeley-function-call-leaderboard`（pinned commit `ea13468e4423454d0c213704fb87cf7cb3990433`，本次 `git rev-parse HEAD` 实测一致）。`native_layout()` 要求 `**/bfcl_eval/constants/model_config.py` **恰好 1 个匹配**，再取 `bfcl_eval/data/BFCL_v3_*.json`。
  - 本次实测：包根在**嵌套一层**的 `.../berkeley-function-call-leaderboard/berkeley-function-call-leaderboard/bfcl_eval`；`model_config.py` 匹配数 = **1**；`BFCL_v3_*.json` = **17 个类别文件**。→ 布局检查可通过。
- **现有脚本/命令**（`reproduction/REPRODUCTION.md` 第 5 节，可直接执行）：

```bash
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python - <<'PY'
import json
from pathlib import Path
from tool_validation import check_native
config = json.loads(Path("reproduction/configs/validation_744.json").read_text())
check_native("bfcl_v3", config["tool_benchmarks"]["bfcl_v3"],
             Path("reproduction/logs/bfcl-preflight-r1"))
PY
```

- **机器配置**：`reproduction/configs/validation_744.json` → `tool_benchmarks.bfcl_v3`：`python_executable=/data/wuxuecheng/miniforge3/envs/rsiflow-bfcl/bin/python`（实测存在，Python 3.11.18）、`repository_root`（存在）、`official_commit`、`evaluator_archive_sha256=c6c3b8ff…`、`categories=all_v3`（会被 `native_layout` 覆盖为实际 17 类）、`version_prefix=BFCL_v3`。
- **预期回执**：输出目录下 `check.json` = `{status:"completed", controlled_rpc_calls:2, response:"test_override_cpu_only", requests:[…2 条…]}`，另有 `spec.json`、`check.log`、`native_work/`（copytree 时忽略 `.git/__pycache__/.env*/result*/score*`）。成功判据是 `len(calls)==2`，否则 `RuntimeError('Official SDK bridge did not complete the CPU test')`。
- **可执行性**：✅ **现有脚本可直接执行**（CPU + bwrap，无需 GPU、无需 API）。
- **仍缺/需注意**：
  1. 这只是**注册/桥接 smoke**，不是 152 条评分。要覆盖「最小评分」需走 `tool_validation.py:104 evaluate_tool()`，它要求 frozen binding、`selected_tasks`（`independent_validation` 且非 `explicit_full_benchmark` 时**恰好 50 条完整任务**）、`HarnessAPI` 真实推理、`generate`+`evaluate` 两阶段、`metric_rows()` 逐类别分母对账。**因此 BFCL 的「最小评分 smoke」与现有 50 条日常口径存在冲突，需要 controller 明确定义**（见第 7 节 B3）。
  2. `tool_benchmarks.bfcl_v3.data_path = assets/cohort/bfcl_v3/test.jsonl` **不存在**（见第 7 节 B1，硬阻塞最终评估）。

### 2.2 HumanEval+（code，164）

- **最小输入**：源数据第 1 条任务（`HumanEval/0`），两个 submission：`canonical = prompt + canonical_solution`、`wrong = prompt + "    return None\n"`；每个 case 的 `data.jsonl` 只含 1 条，强制官方分母为 1。
- **官方执行器**：EvalPlus pinned commit `26d6d00bb1fd0fa37f39c99d5290da67891d1c5e`（与 `evalplus_isolated.py:30 PINNED_COMMIT` 一致），`release_version=v0.1.10`；命令由 `reporting.py:161 official_command()` 生成：`python -m evalplus.evaluate --dataset humaneval --samples … --parallel 1 --version v0.1.10 --output-file official_results.json --test-details`。
- **隔离**：`reporting.py:236 native_ipc` → code 类走 `evalplus_isolated.evaluate_evalplus_isolated()`，内部 `LinuxSandbox()`（`evalplus_isolated.py:243 LinuxSandbox().validate()`、`:285 worker_isolation=LinuxSandbox().backend`）。**必须显式 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`**，否则走 legacy Landlock（本机 errno 38）。
- **现有脚本**：`reproduction/scripts/evalplus_bwrap_smoke.py`（`load_spec()` 取 `configured["humaneval_plus"]`，`run_smoke()` 自检 env backend，`run_case()` 校验 returncode/backend/source_commit/source_sha256/分母=1）。

```bash
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow:$PWD/reproduction/scripts" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  reproduction/scripts/evalplus_bwrap_smoke.py \
  --config reproduction/configs/report-evaluators-v4.json \
  --output reproduction/logs/evalplus-preflight-r1
```

- **机器配置**：`reproduction/configs/report-evaluators-v4.json` → `evaluators.humaneval_plus`：`entrypoint=assets/evaluators/evalplus/evalplus/evaluate.py`、`data_path=assets/raw/evalplus/HumanEvalPlus.jsonl`、`task_ids_path=assets/report-specs-v4/humaneval_plus_ids.json`、`python_executable=…/envs/rsiflow/bin/python`。本次实测：entrypoint/data/ids/python **全部存在**，`entrypoint_sha256` **匹配**（`47f766df…`）；数据 **164 行**、ids **164**；首条含 `prompt`/`canonical_solution`/`task_id`。
- **预期回执**：`smoke_report.json` = `{schema_version:1, scope:"official_one_task_smoke_not_a_benchmark_score", backend:"linux_bwrap_seccomp_v1", source_commit, source_sha256, task_id, cases:{canonical:{status:"official_pass", base_status/plus_status:"pass", worker_isolation, official_results_path, official_results_sha256, data_sha256, prediction_sha256}, wrong:{status:"official_fail", …}}, ok:true}`；每个 case 目录另有 `official_results.json`。`ok` 仅在 canonical=official_pass 且 wrong=official_fail 时为 true。
- **可执行性**：✅ **现有脚本可直接执行**（CPU + bwrap）。依赖 `datasets`（实测已安装 = True，即历史修复后的状态）。
- **仍缺**：无材料缺口；但**当前 HEAD 未重跑**（历史证据属 `pt-bfkp5k0d`，见第 8 节）。

### 2.3 MBPP+（code，178）

- **最小输入（应有口径）**：源数据第 1 条（`Mbpp/2`）canonical/wrong 两个 submission，分母 1；`release_version=v0.2.0`，`--dataset mbpp`。
- **官方执行器**：同 EvalPlus pinned commit；`official_command()` 对 MBPP+ 走 `--dataset mbpp --version v0.2.0`。
- **现有脚本**：
  - ❌ `reproduction/scripts/evalplus_bwrap_smoke.py` **硬编码 `configured["humaneval_plus"]`**（`load_spec()`），argparse 只有 `--config`/`--output`，**没有 benchmark 选择参数** → **不能原样用于 MBPP+**。
  - ✅ `RSIFlow/runtime/scripts/verify_native_report_adapters.py` **已覆盖 mbpp_plus**：先对全部 378 行做 `mbpp_deserialize_inputs` + `encode()` 输入 codec 审计（`all_inputs_codec_audit.json`），再取第 1 条 canonical 走 `evaluate_evalplus_isolated`，要求 `base_status == plus_status == "pass"`。
- **机器配置**：`report-evaluators-v4.json` → `evaluators.mbpp_plus`：`data_path=assets/raw/evalplus/MbppPlus.jsonl`（实测 **378 行**，含 `prompt`/`canonical_solution`/`assertion`）、`task_ids_path=assets/report-specs-v4/mbpp_plus_ids.json`（实测 **178**）、entrypoint sha 匹配、`release_version=v0.2.0`。
  - 注意：源文件 378 行 vs 冻结 ids 178 条 —— 正式评估分母由 `task_ids_path`/冻结 manifest 决定（`reporting.py:268` 校验 `set(raw["eval"]) == set(ids)`），smoke 用 1 条子集。
- **预期回执**：`verification.json` 中 `tests.mbpp_plus = {native_tasks:378, input_cases_checked:N, fixture:"Mbpp/2", base_and_plus_fixture_passed:true, candidate_source:"official first canonical solution, test_override only", status:"passed"}`，顶层 `model_calls:0`、`is_formal_benchmark:false`、`eligible_for_sft:false`。
- **可执行性**：⚠️ **需要新增适配**（二选一，均需 controller 批准，本次未改任何代码）：
  - 方案 A（推荐，最小改动）：给 `evalplus_bwrap_smoke.py` 增加 `--benchmark {humaneval_plus,mbpp_plus}` 参数并让 `load_spec()` 按参数取 spec，从而复用 canonical/**wrong** 双向判据（MBPP+ 的 wrong 构造需确认 `prompt + "    return None\n"` 对 MBPP 断言型任务仍产生 FAIL）。
  - 方案 B（零代码改动）：直接用 `verify_native_report_adapters.py --specs reproduction/configs/report-evaluators-v4.json`，但它**只有 canonical 正向判据、没有 wrong 负向判据**，且 QA 部分用空答案（见 2.4/2.5）。
- **仍缺**：MBPP+ 的**负向（wrong）官方 FAIL 判据**目前没有现成脚本覆盖。

### 2.4 HotpotQA-dev（searchqa，100）

- **最小输入（现有脚本口径）**：`assets/report-specs-v4/hotpotqa_dev.json` 的第 1 条 gold（含 `_id`/`answer`/`question`/`context`/`supporting_facts`），submission 为**空答案** `{answer:{_id:""}, sp:{_id:[]}, evidence:{_id:[]}}`，只验证官方脚本可执行 + 指标可解析。
  - 若要做「单条金答案」smoke（PROGRESS 记载的 EM=F1=0.01 口径）：submission 填该条真实 answer，gold 文件保持 **100 条固定分母** → EM=F1=1/100=0.01。**该 gold 版本目前没有专用脚本**，属需新增适配。
- **官方执行器**：`third_party/report_evaluators/hotpot/hotpot_evaluate_v1.py`，pinned repo commit `3635853403a8735609ee997664e1528f4480762a`，`protocol=official_hotpot_v1`。命令：`[python, entrypoint, predictions, data_path]`（`reporting.py:163-167`）。
- **指标解析**：`reporting.py:183-190 parse_official_metrics()`：HotpotQA `scale=1.0`，`EM=raw["em"]/1.0`、`F1=raw["f1"]/1.0`，并强制 0≤值≤1 且非 bool。
- **隔离口径（重要）**：QA  comparator **不走 bwrap**。`reporting.py:259-263` 对非 code 类用 `subprocess.run(command, env={"PATH":…,"LANG":"C.UTF-8","PYTHONNOUSERSITE":"1"})`，即「受信评测器 + 干净环境子进程」，因为不含不可信候选代码。所以 QA 的最小 smoke **不验证 bwrap 隔离**，这是设计如此，不能据此宣称 QA 已隔离验证。
- **现有脚本**：`RSIFlow/runtime/scripts/verify_native_report_adapters.py`（`else` 分支）。**注意 `--specs` 默认值是旧配置 `configs/report-evaluators.json`，必须显式传 v4**：

```bash
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  RSIFlow/runtime/scripts/verify_native_report_adapters.py \
  --specs reproduction/configs/report-evaluators-v4.json \
  --output reproduction/logs/report-adapters-preflight-r1
```

（该脚本一次覆盖 4 个 official evaluator：humaneval_plus、mbpp_plus、hotpotqa_dev、2wiki_dev；`--output` 目录必须不存在，`mkdir(parents=True, exist_ok=False)`。）

- **机器配置**：`report-evaluators-v4.json` → `hotpotqa_dev`：`data_path=assets/report-specs-v4/hotpotqa_dev.json`（实测 **100 条**）、`task_ids_path=…_ids.json`（**100**）、entrypoint 存在且 `entrypoint_sha256` 匹配（`d35fc91a…`）、`python_executable` 存在。
- **预期回执**：`verification.json` → `tests.hotpotqa_dev = {native_script_executed:true, fixture_empty_answer_metrics:{EM:…,F1:…}, status:"passed"}`；目录内 `fixture_gold.json`、`fixture_submission.json`、`stdout.txt`/`stderr.txt`（经 `official_command` 子进程）。整体 `status=CPU_NATIVE_ADAPTERS_PASSED` 且 `failures={}` 时 exit 0。
- **可执行性**：⚠️ **脚本已存在但口径需确认/适配**：空答案 fixture 只证明「官方脚本跑通 + 指标可解析」，不证明金答案判分正确。若 preflight 要求「单条金答案 EM=F1=0.01」口径，需新增 gold fixture 适配（不改官方 comparator）。
- **仍缺**：gold-answer 最小 smoke 的专用脚本/参数；无数据缺口。

### 2.5 2Wiki-dev（searchqa，100）

- 与 2.4 同构，差异点：
  - 官方执行器 `third_party/report_evaluators/2wiki/2wikimultihop_evaluate.py`，pinned commit `13800e5be57df1b4040b9b1588c6c811779e69e9`，`protocol=official_2wiki_original_v1`。
  - `reporting.py:165-167`：**只有 protocol ≠ official_2wiki_original_v1 时才追加 `alias_path`**；当前 v1 协议**不传 alias**（与 PROGRESS「2Wiki 保持上游 v1，不为 aliases 改协议」一致）。
  - `parse_official_metrics()`：**2Wiki `scale=100.0`**（官方输出百分制），`EM=raw["em"]/100`、`F1=raw["f1"]/100`；HotpotQA 是 1.0。两者 scale 不同，不能混用。
  - `OfficialEvaluatorSpec.validate()`（`reporting.py:102`）对 2Wiki 协议有额外约束。
- **机器配置实测**：`data_path=assets/report-specs-v4/2wiki_dev.json`（**100 条**，含 `_id`/`answer`/`evidences`/`question`/`supporting_facts`）、ids **100**、entrypoint 存在且 sha 匹配（`854b03c4…`）。
- **预期回执**：`tests.2wiki_dev = {native_script_executed:true, fixture_empty_answer_metrics:{EM,F1}, status:"passed"}`。
- **可执行性 / 仍缺**：同 2.4（⚠️ 需确认空答案 vs 金答案口径）。

### 2.6 ACEBench（50，**不在五类内，pending**）

- 官方执行器：`assets/evaluators/acebench/AgentSuite/ACEBench`（存在），`data_all/data_en` 实测 **17 个 `data_*.json`**；`category=test_all`、`language=en`。
- **外部依赖（凭据只引用变量名，不读取值）**：`user_api_key_env=ACE_USER_API_KEY`、`user_base_url_env=ACE_USER_BASE_URL`、`user_model=user_provider_model=DeepSeek-V4.1-Flash`。这是**独立于 Meta `OPENAI_API_KEY` 的第二套凭据**，当前未注入 → ACE 无法执行。
- 协议约束：`evaluation_manifest.py:15-16` 要求 `normal_multi_turn_*` 必须带 `native_ids`（完整对话，不能按 turn 评）；`tool_validation.py:125` 要求子集不得拆散完整对话。本次实测 v4 manifest 中 6 条 `normal_multi_turn_*` 均带 `native_ids`（各 2 条）、`task_unit=complete_conversation` → 满足。
- 现状：`skip_acebench=true`，`ace_skip_summary()` 强制 `scores/acebench/result.json` 不存在、`acebench_status=incomplete_requires_audit`。**不得把 694 写成 744 完成。**

## 3. 已有脚本可执行 vs 需要新增适配（汇总）

| benchmark | 现有脚本 | 直接可执行？ | 需要的适配 |
| --- | --- | --- | --- |
| BFCL-v3 | `tool_validation.check_native`（REPRODUCTION.md 第 5 节内联命令） | ✅ 是（零推理、2 RPC） | 若要「最小评分」需定义 `evaluate_tool` 子集口径（与 50 条日常约束冲突） |
| HumanEval+ | `reproduction/scripts/evalplus_bwrap_smoke.py` | ✅ 是（canonical+wrong） | 无 |
| MBPP+ | 同上脚本**硬编码 humaneval_plus**；`verify_native_report_adapters.py` 覆盖但只有正向 | ⚠️ 否 | 加 `--benchmark` 参数（方案 A）或接受仅正向 fixture（方案 B）；补 wrong 负向判据 |
| HotpotQA-dev | `verify_native_report_adapters.py`（空答案 fixture） | ⚠️ 口径待定 | 若要求金答案 EM=F1=0.01，需新增 gold fixture 适配 |
| 2Wiki-dev | 同上 | ⚠️ 口径待定 | 同上；注意 scale=100 与 v1 不传 alias |
| 四卡 Qwen 健康 | `reproduction/scripts/model_service_smoke.py` | ✅ 脚本齐备 / ❌ 本机无 GPU | 必须在 SCO GPU worker 上执行 |
| 694 `--check` | `RSIFlow_4B/launch.py --check --config …` | ✅ 是（本次已执行，exit 0） | 需补充 694/评估器/GPU 的额外预检（`--check` 不覆盖） |

**一次覆盖 4 个 official evaluator 的现成入口**（零代码改动，但口径为「codec 审计 + 首条 canonical 正向 + QA 空答案」）：

```bash
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  RSIFlow/runtime/scripts/verify_native_report_adapters.py \
  --specs reproduction/configs/report-evaluators-v4.json \
  --output reproduction/logs/report-adapters-preflight-r1
```

## 4. 当前 694 `--check`：准确命令、实测输出与依赖

### 4.1 准确命令（必须显式传机器配置）

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow
PYTHONDONTWRITEBYTECODE=1 /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  RSIFlow_4B/launch.py --check \
  --config /data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/configs/train_4b.json
```

- **为什么必须带 `--config`**：`RSIFlow/launcher.py:20` 默认 `ROOT/configs/train_4b.json`，即**上游占位配置**（`/path/to/...`、`data_release=rounds_3000_5x600`、`skip_acebench=false`）。不带 `--config` 巡检的不是 694 机器配置。
- 转发链：`RSIFlow_4B/launch.py` → `launcher.main('4b')` → `subprocess.run([sys.executable, launch_meta.py, --config <resolved>, *remaining])`；`--check` 属 `remaining`。`--prepare-data` 在含 `--check` 时被跳过（`launcher.py:21`），**不会触发数据重打包**。

### 4.2 本次实测输出（HEAD `9f4c262`，exit 0，无写入、无 API）

```json
{
  "config": ".../reproduction/configs/train_4b.json",
  "run_dir": ".../Rollout_logs/runs/rsiflow_skill_4b_600x5_eval694",
  "project_root": ".../RSIFlow",
  "controller_exists": true,
  "model_config_exists": true,
  "checkpoint_path": ".../assets/models/Qwen3-4B",
  "checkpoint_exists": true,
  "frozen_data_exists": true,
  "catalog_exists": true,
  "rounds": 5,
  "training_tasks_per_round": 600,
  "meta_skill_seed_path": null,
  "meta_skill_seed_exists": null,
  "meta_skill_context_chars": 24000,
  "wait_for_gpus": false
}
```

### 4.3 `--check` **不**覆盖什么（必须另做预检）

`launch_meta.py:507-529` 只巡检：controller 文件、base_config 文件、checkpoint 目录、`frozen_data_dir` 目录、codex catalog 文件、rounds/tasks 数、skill seed。**它不检查**：`skip_acebench`/694 口径、`validation_manifest`、`official_specs` 与评测器哈希、`tool_benchmarks` 数据、检索索引、GPU 可用性、API 凭据、`validation_sources` 快照。
→ **`--check` 返回 0 不等于 694 协议或评估链路已验证**。694 的真实校验点是 `validation_skip.evaluation_scope()`/`ace_skip_summary()` 与真实 release/机器配置测试（PROGRESS 记载 4/4），以及第 6 节的 `validate.py` 前置。

### 4.4 外部资产依赖（本次逐项只读实测）

| 依赖 | 配置来源 | 实测 |
| --- | --- | --- |
| Qwen3-4B checkpoint | `base_4b.json:task_checkpoint` | ✅ 存在；`reproduction/audit/model-manifest.json` 记录 **13 文件 / 8,060,926,626 bytes / 3 个 safetensors 分片**，逐文件存在性与字节数 **0 missing / 0 mismatch** |
| base_config | `train_4b.json:base_config` → `reproduction/configs/base_4b.json` | ✅ 存在 |
| codex catalog | `train_4b.json:codex_model_catalog` → `reproduction/configs/gpt-6-astra.json` | ✅ 存在 |
| codex host config | `reproduction/configs/codex_host.toml` | ✅ 存在 |
| Codex binary/source/provenance | `base_4b.json:meta.codex_executable/codex_source/provenance_file` | ✅ 三者均存在；binary SHA256 固定 `56ef98ab…`、commit `3d2ee51c…` |
| 冻结训练数据 | `frozen_data_dir=…/rounds_3000_5x600_v4` | ✅ 存在；READY `config_hash=22305bf1…`、3000=5×600 |
| 冻结验证 manifest | `validation_manifest=…/validation_744_v4/manifest.json` | ✅ 存在；744 条、6 个 pack 文件 |
| 检索索引 | `train_4b.json:retrieval_index=assets/retrieval-v3/search.sqlite` | ✅ 存在（注意是 **v3**，不是早期 v2） |
| EnvScaler 环境/utils | `base_4b.json:envscaler_root`、`envscaler_utils` | ✅ 均存在（`assets/cohort/envscaler`、`assets/evaluators/envscaler/.../env_util.py`，commit `96ae8b02…`） |
| seed harness / source manifest | `base_4b.json:seed_harness`、`source_manifest` | ✅ 均存在 |
| pinned bwrap + receipt | `assets/isolation/bin`、`install-receipt.json` | ✅ 存在；binary SHA256 `d7880722…a12fca`，receipt 的 manifest/launcher 路径与哈希匹配 |
| `validation_vault` | `train_4b.json:validation_vault=evaluation_vault/reconstructed` | ❌ **不存在**；但 `task_adapter.py:81` 优先用显式 `validation_manifest`，故当前不阻塞（若删掉 `validation_manifest` 则会落到该缺失路径） |
| `output_root` | `Rollout_logs` | ❌ 不存在；`launch_meta.py` 正式运行会 `run_dir.mkdir(parents=True)` 创建，`--check` 不创建 |
| `dataset_root` 下的 `datasets`/`environment_files` | `train_4b.json` 指向 `assets/raw/train/tool_use/envscaler/…` 等 7 个路径 | ❌ **7 个全部不存在**；`assets/raw/` 实际布局是 `envscaler-191-env`、`envscaler-rl-scenario`、`envscaler-sft-scenario`、`deepcoder-taco`、`hotpot`、`2wiki`、`bfcl_v3`、`evalplus` 等。运行时用冻结 v4 数据（`data_dir=frozen_data_dir`）故不阻塞五轮，但**任何 `--prepare-data`/重打包都会失败**，属配置与资产布局脱节 |
| Meta API 凭据 | `base_4b.json:meta.api_key_env=OPENAI_API_KEY`（provider `OpenAI`、model `gpt-6-astra`、base_url `http://token.acerobotics.com/v1`、`allow_model_fallback=false`） | 未读取/未验证注入；`meta_api_smoke` 仍为 blocked_missing_credentials |
| ACE 用户模拟凭据 | `ACE_USER_API_KEY` / `ACE_USER_BASE_URL` | 未注入（ACE pending） |

### 4.5 有效配置覆盖（避免误读 base JSON）

`RSIFlow/task_adapter.py:40-84 _config()`（launcher JSON 分支的覆盖逻辑在 `:56-84`）会覆盖 base：
- `data_dir = frozen_data_dir`（v4 胜出，base 的 `rounds_3000_5x600` 失效）；
- `ports=[8271,8272,8273,8274]` → `task_base_url=http://127.0.0.1:8271/v1`，`task_replicas` 按 ports 重建为 GPU 0/1/2/3（base 里的 8071/8074/8072/8073 **失效**）；
- **`trainer_python = sys.executable`** → base 的占位 `/path/to/envs/rsiflow/bin/python` 被覆盖。**因此必须用 rsiflow 环境的 python 启动 launcher**，否则服务与 SFT 会用错解释器；
- `window_quotas`/`probe_per_domain` 由 `evolution_protocol.training_quotas()` 得出（200/200/200），覆盖 base 的 256/1/1；
- `experiment_scope="multidomain"`、`training_schedule="round_disjoint"`、`search_dev_fraction=0`、`max_generations=rounds=5`；
- `base["training"].update(SFT_PRESET, seed=42)` → 有效 LoRA rank 8 / alpha 16 / lr 2e-6 / `max_steps=-1` / 1 epoch / 7 个 target modules（base 的 rank16、lr2e-5、max_steps32、4 modules **失效**）；
- `round_protocol = {data_dir, train_quotas_per_round, validation_manifest, minimum_sft_samples, skip_acebench}`。

## 5. 四卡 Qwen 健康 smoke：准确命令与依赖

### 5.1 命令（脚本已存在）

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  reproduction/scripts/model_service_smoke.py \
  --output-root reproduction/logs/model-service-preflight-r1
```

- `reproduction/scripts/model_service_smoke.py`：`CONFIG = ROOT/"reproduction/configs/train_4b.json"`（**已指向 694 机器配置**）；`TaskAdapter._config()` 生成有效配置 → `start_services()` → `verify_services()` → `LocalTaskClient` 发一条 `"Reply with exactly RSIFLOW_SMOKE_OK"`（`seed=42, max_tokens=16, temperature=0`）→ 断言回答含 `RSIFLOW_SMOKE_OK` → `finally: stop_services()`。
- **不需要** `RSIFLOW_SANDBOX_BACKEND`（服务不在沙箱内）；**需要真实 GPU**。

### 5.2 代码级健康判据（不是只看端口存活）

- `RSIFlow/runtime/scripts/start_multidomain_service.py:24`：必须显式 `--enable-gpu`；`:40-43`：`nvidia-smi` 查 `memory.free`，**对应卡 < 20000 MiB 直接失败**。
- `gpu_phases.py:83 start_services()`：逐 replica 起 `trainer_python -B runtime/scripts/start_multidomain_service.py --config <four_gpu/gpu_N.json> --enable-gpu`；子进程环境**剥离所有名字含 KEY/TOKEN/PASSWORD/SECRET/AUTH 的变量**；轮询 `/health`，要求 `ready==true` **且** `visible_devices == str(gpu)` **且** `bindings[checkpoint] == {checkpoint_path: resolved, weights: checkpoint_manifest(...)}`；若 ready 但绑定不符 → `RuntimeError('New service loaded an unexpected checkpoint or GPU')`；已有存活 owner → `RuntimeError('Existing inference owner must be reconciled, not duplicated')`。
- `gpu_phases.py:138 verify_services()`：要求 **恰好 4 个 replica 且 GPU 集合 == {0,1,2,3}**，逐卡复核 weights/GPU 绑定并返回 `model_fingerprint`。
- `gpu_phases.py:57-61 stop_services()`：killpg 后轮询直到 **GPU 0–3 每卡 ≥ 40000 MiB 空闲**（60s 上限），否则 `RuntimeError('Inference exited but four training GPUs did not release their memory')`。
- 服务栈：`serve_gpu.py` 用 **transformers `AutoModelForCausalLM` + FastAPI + torch**（**不是 vLLM**）。

### 5.3 预期回执

`<output-root>/result.json`：
```json
{"status":"completed","checkpoint":"…/assets/models/Qwen3-4B","replicas":[4 项 gpu/base_url],
 "answer":"…RSIFLOW_SMOKE_OK…","usage":{…},
 "services_stopped_and_training_memory_released":true,
 "scope":"four_real_bf16_services_and_one_short_generation_not_a_benchmark"}
```
另有 `effective_config.json`、`receipts/`、`four_gpu/gpu_{0..3}.json` 与 `gpu_{0..3}.log`、服务 registry。**这是诊断，不是 benchmark 分数。**

### 5.4 外部依赖与本机实测结论

| 依赖 | 实测 |
| --- | --- |
| 4 张逻辑 GPU 0–3，每卡 ≥20000 MiB 空闲（SFT 前 ≥40000 MiB） | ❌ **本机 `nvidia-smi --query-gpu=…` 返回 `Failed to initialize NVML: Unknown Error`** → 当前容器/开发机**无可用 GPU**，该 smoke **不能在本机执行** |
| `nvidia-smi` 可执行 | ✅ `/usr/bin/nvidia-smi` 存在（但 NVML 初始化失败） |
| rsiflow 环境 serving 依赖 | ✅ `torch/transformers/accelerate/peft/fastapi/uvicorn/datasets` 均可导入；`vllm/trl/ray` 不存在但**服务栈不需要** |
| `torchrun`（SFT 4-rank DDP 用） | ✅ `…/envs/rsiflow/bin/torchrun` 存在；`train_four_gpu_phase.py:53` 用 `--nnodes=1 --nproc_per_node=4 --max_restarts=0`，`train_task_meta_sft.py:237` `init_process_group('nccl')` |
| Qwen3-4B 权重与 manifest | ✅ 13 文件 / 3 分片 / 字节数全匹配（见 4.4） |
| 端口 8271–8274 可用 | 未验证（需 GPU 主机上实测） |

→ **必须在 SCO GPU worker（A800 4 卡或已授权的 A100）上执行**，且遵守 AGENTS.md 第 9 节提交纪律；本机只能做 CPU 侧预检。

## 6. 最终 694 评估的真实入口与前置（盘点，不执行）

`RSIFlow/validate.py` 是最终评估编排器，CLI：

```bash
python RSIFlow/validate.py \
  --snapshot <frozen_task_snapshot.json> \
  --pipeline-config <effective pipeline config.json> \
  --config reproduction/configs/validation_744.json \
  --role independent_validation \
  --manifest RSIFlow/runtime/data/validation_744_v4/manifest.json \
  --skip-ace --execute
```

关键前置与约束（逐条来自代码）：
1. `validate.py:11-12`：`skip_ace` 只允许 `role=independent_validation` 且非 `full_benchmark`。
2. `validate.py:23-25` → `from validation_sources import verify` 后 `verify(settings, output_root/'validation_sources.json')`：**这是硬阻塞点**，见第 7 节 B1。
3. `validate.py:29-38`：必须有显式 role 与冻结 manifest；**输出目录必须在训练 run 目录之外**（`:31` `out.resolve().is_relative_to(pipeline_path.parent)` → raise）。
4. `evaluation_manifest.py:8-21 select_specs()`：`Counter(source)` 必须**精确等于** `validation_limits`（152/50/164/178/100/100），且 `set(groups) == set(benchmark_ids)`（**即使 skip ACE，manifest 仍须含全部 6 源**）；ACE `normal_multi_turn_*` 必须带 `native_ids`；逐 pack 校验 `record_sha256`、拒绝 symlink。
5. `evaluation_manifest.py:17-18`：`role=final_test` 要求 `clean_independence_audited` 为真且所有 `exposure=='unexposed'`。**v4 manifest 实测 `clean_independence_audited=False`、744 条全部 `exposure_unknown` → `final_test` 角色当前不可用**，只能 `independent_validation`。
6. `validate.py:36-38`：`role=final_test` 还要求 `final_state.json` 的 `rounds_completed == 3` 且 `status=='completed'` —— **与本协议 5 轮硬编码不一致**（潜在缺陷；当前 `final_only` + `independent_validation` 路径不触发，但必须在启用 `final_test` 前解决，且不得靠改上游断言掩盖）。
7. `validate.py:57-58`：`ensure_services()` + `verify_services()` → **最终评估本身就需要四卡 Qwen 服务**，随后 `generate_dynamic` 产生真实预测。
8. `validate.py:49-50` → `validation_skip.reject_complete_cache_for_skip()`：skip-ACE 不得复用含 `complete.json` 的旧输出目录。
9. `validate.py:106-111`：skip-ACE 成功路径写 `code_search_partial_complete.json`（`officially_scored=694`、`expected_total=744`、`benchmarks_completed` 为 5 个名字），**不写 `complete.json`**。
10. 评分侧：code 类走 bwrap 隔离 worker；QA 类走受信干净环境子进程；tool 类走 `evaluate_tool` 的 native 隔离 + `HarnessAPI`。

## 7. 仍缺材料 / 不一致清单（按阻塞强度排序）

### B1（**硬阻塞，最终 694 评估无法启动**）`tool_benchmarks.data_path` 指向不存在的文件

- `reproduction/configs/validation_744.json`：
  - `bfcl_v3.data_path = …/assets/cohort/bfcl_v3/test.jsonl` → **不存在**
  - `acebench.data_path = …/assets/cohort/acebench/test.jsonl` → **不存在**
- 消费点：`RSIFlow/validation_sources.py:19 add(spec['data_path'])` 对**每个** tool benchmark 无条件哈希该路径；`validate.py:23-25` 在评估一开始就调用 `verify()` → `snapshot()` 会因文件缺失抛错。
- 注意：`tool_validation.native_layout()` 用的是 `repository_root` 内的官方数据（BFCL 17 个类别文件实测存在），**所以 `check_native` 不受影响**；受影响的是 `validation_sources` 快照与最终评估。
- 真实候选位置（本次实测存在，供 controller 决策，**本次未改任何配置**）：
  - `assets/cohort/allocated/validation/bfcl_v3.jsonl`（**152 行**）、`acebench.jsonl`（**50 行**）—— 与冻结配额完全一致；
  - `assets/cohort/normalized/validation/bfcl_v3.jsonl`（5250 行）、`acebench.jsonl`（1023 行）—— 全量归一化池；
  - `RSIFlow/runtime/data/validation_744_v4/{bfcl_v3,acebench}.jsonl` —— 冻结 pack。
- 处置建议：由 controller 决定把 `data_path` 指向哪一层（并同步 `validation_sources` 快照重建），**不得为了跑通而删除校验或改上游断言**。

### B2（**硬阻塞，四卡 smoke 与最终评估**）本机无可用 GPU

`nvidia-smi` → `Failed to initialize NVML: Unknown Error`。必须在 SCO GPU worker 上执行；本机无法完成第 5 节与第 6 节第 7 条。

### B3（口径缺口）「最小 smoke」定义未统一

- BFCL 现有 `check_native` 是**零推理注册检查**；`evaluate_tool` 的 `independent_validation` 路径要求**恰好 50 条完整任务**。二者都不是「152 条最小评分」。需要 controller 明确：五类最小 smoke 是「适配器/桥接级」还是「小分母真实评分级」，并据此决定是否需要新增子集入口。
- MBPP+ 缺**负向 wrong→FAIL** 判据脚本（2.3）。
- HotpotQA/2Wiki 现有 fixture 是**空答案**，与 PROGRESS 记载的「单条金答案 EM=F1=0.01」不是同一口径（2.4/2.5）。

### B4（凭据缺口）ACE 用户模拟 API 未注入

`ACE_USER_API_KEY`/`ACE_USER_BASE_URL`（`DeepSeek-V4.1-Flash`）未提供 → ACE 50 保持 pending；不得计入 694，也不得宣称 744 完成。Meta `OPENAI_API_KEY` 同样未验证注入（`meta_api_smoke` 仍 blocked）。

### B5（配置与资产布局脱节）`datasets`/`environment_files` 7 个路径全部不存在

见 4.4。运行时靠冻结 v4 数据不阻塞，但 `--prepare-data`/重打包必失败；`data_release` 与 `frozen_data_dir` 已同为 v4（一致），而 `base_4b.json:data_dir` 仍写 v1（被覆盖）。建议清理以免误用。

### B6（潜在协议缺陷）`validate.py` 的 `final_test` 硬编码 `rounds_completed == 3`

与 5 轮协议冲突；且 `final_test` 还要求 `clean_independence_audited`/`exposure=unexposed`，v4 数据不满足。当前路径不触发，但启用前必须解决。

### B7（数据保留意见，必须随证据公开）

v4 manifest `clean_independence_audited=False`、744 条 `exposure_unknown`、`version=reconstructed-v1`；EnvScaler train/heldout 共享 191 个环境定义（上游 `failed_overlap`）。→ 只能称「同协议重构」，不能称精确复现或干净独立集。

### B8（已核对为**不是**阻塞的项目，避免误判）

- BFCL 包根嵌套一层：`native_layout` 用 `**/bfcl_eval/constants/model_config.py` glob，实测**恰好 1 个匹配**、17 个 `BFCL_v3_*.json` → 正常。
- `evalplus` 模块在 rsiflow 环境 `find_spec` 为 False：属预期，`load_pinned_evalplus()` 从 pinned source 树加载；`datasets` 已安装。
- `validation_vault` 缺失：被显式 `validation_manifest` 覆盖，不阻塞。
- `Rollout_logs` 缺失：正式运行时创建，`--check` 不创建。
- 冻结验证数据完整性：本次用**仓库自身的** `data_protocol.public_text/text_hash/fingerprint` 语义复核，**744/744 全部通过**（738 条单记录 prompt 投影哈希 + 6 条 ACE 完整对话 `fingerprint(member hashes)`）；6 个 pack 文件 `record_sha256` 全匹配、0 缺失。
  - 方法学提醒：`content_hash` **不是**记录原始字节的 sha256，直接按 raw bytes 比对会得到 744 条「全不匹配」的**假警报**；ACE 完整对话的 `record_parts` 在打包时被剔除，需按 `{id, native_records}` 结构复核。

## 8. 历史 smoke ≠ 当前通过（归属表）

| 证据 | job | 覆盖范围 | 归属 commit / 状态 |
| --- | --- | --- | --- |
| Candidate | `pt-gyfupguq` | 7 functional + 18 negative + 5 timeout + 3 session | 历史 accepted；最终 A100 combined 中 candidate exit 0 |
| EvalPlus | `pt-bfkp5k0d` | **仅 1 个 HumanEval+ 任务** canonical/wrong | **未在 `313b6bc` 及其后（含当前 HEAD `9f4c262`）重跑**；不是 HumanEval+ 分数 |
| BFCL | `pt-fdvsxqax` | **仅 2 次受控 CPU RPC**，零推理 | 历史 accepted；最终 combined 中 BFCL exit 0；不是 BFCL 分数 |
| Persistent Meta | `pt-zj1l9q1t` | dynamic tools + restart/resume | **外部 actor `huangsiyuan1`、8×A100、`root_mapping=true`**，仅历史 |
| 最终 combined | `pt-v6m10c4t` | candidate/Harness/BFCL exit 0 | **整体/worker FAILED**（Meta 外部 provider completion failure），不得写成 SUCCEEDED |
| 最终 Meta gate | `pt-x6b4ista` | Meta-only retry，8×A100、`root_mapping=false` | SUCCEEDED/worker Succeeded/exit 0；当前唯一 Meta gate |
| 四卡模型诊断 | `pt-ox3o5he2` | 四个 BF16 A800 服务 + 短生成 + 释放 | 历史诊断；**当前无服务运行**，不是 benchmark |
| 离线回归 | — | 297 passed / 2 skipped | 功能工作树预检 92.22s；合并后主工作树 89.15s（先经历 13 failed → installer 恢复 → 51 passed） |

**没有任何五轮、SFT、最终 694/744、8B 或模型 benchmark 分数。没有 A800 Meta pass。Codex native-shell descendant hygiene 未验证，只允许 `shell_tool=false` 的 construction-level gate。**

## 9. 建议的最小关闭顺序（供 controller 决策，本次未执行）

1. **先解 B1**：确定 `tool_benchmarks.data_path` 的正确层级并重建 `validation_sources` 快照（配置改动需批准）。这是最终评估能否启动的前置。
2. **统一 B3 口径**：书面定义五类「最小 smoke」是适配器级还是小分母评分级；据此决定 MBPP+ 是否加 `--benchmark` 参数、QA 是否加 gold fixture、BFCL 是否需要子集评分入口。
3. **CPU 侧先跑可执行项**（本机即可，无需 GPU/API）：
   - HumanEval+：`evalplus_bwrap_smoke.py`（新输出目录）；
   - BFCL：`check_native` 内联命令（新输出目录）；
   - 4 个 official evaluator：`verify_native_report_adapters.py --specs …report-evaluators-v4.json`（新输出目录）。
   全部显式 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`，输出落在 Git 忽略的 `reproduction/logs/`，每次新目录，记录 report SHA256。
4. **GPU 侧（SCO worker）**：`model_service_smoke.py --output-root …`，确认 4 卡绑定/weights manifest/≥20000 MiB 与停止后 ≥40000 MiB 释放；随后才谈四卡服务门。
5. **凭据**：安全注入 `OPENAI_API_KEY`（Meta）与 ACE 用户模拟凭据（若要关闭 ACE 50）；不写入仓库/网页/日志。
6. **补 `--check` 之外的预检**：694 口径（`evaluation_scope`）、评测器哈希、`validation_sources` 快照、检索索引、GPU 健康。
7. **解决 B6**（`final_test` 的 3 轮硬编码与 exposure 要求）后才考虑 `final_test` 角色；当前只用 `independent_validation`。
8. **最后**做独立 experiment preflight review；在此之前不得进入 A0+B1–B5、SFT 或最终评估。

## 附录 A：本次只读核验命令（可复现）

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow

# 1) 694 --check（唯一执行的命令；无写入、无 API）
PYTHONDONTWRITEBYTECODE=1 /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  RSIFlow_4B/launch.py --check --config "$PWD/reproduction/configs/train_4b.json"

# 2) 评测器/spec/数据存在性与 pinned 哈希
PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
import json, hashlib
from pathlib import Path
sha=lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
cfg=json.load(open("reproduction/configs/report-evaluators-v4.json"))["evaluators"]
for k,v in cfg.items():
    for f in ("entrypoint","data_path","task_ids_path","python_executable"):
        print(k,f,Path(v[f]).exists())
    print(k,"entrypoint_sha_match",sha(v["entrypoint"])==v["entrypoint_sha256"])
val=json.load(open("reproduction/configs/validation_744.json"))
for k,v in val["tool_benchmarks"].items():
    for f in ("data_path","repository_root","python_executable"):
        print(k,f,Path(v[f]).exists())
PY

# 3) BFCL 包根/类别文件与 pinned commit
find assets/evaluators/bfcl-official -name model_config.py -path "*bfcl_eval/constants*"
ls assets/evaluators/bfcl-official/berkeley-function-call-leaderboard/berkeley-function-call-leaderboard/bfcl_eval/data/BFCL_v3_*.json | wc -l

# 4) 冻结验证数据完整性（必须用仓库自身语义，不能用 raw-byte sha256）
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -B - <<'PY'
import json
from pathlib import Path
import data_protocol as dp
m=json.load(open("RSIFlow/runtime/data/validation_744_v4/manifest.json"))
ok=0
for t in m["tasks"]:
    with open(t["record_file"],"rb") as f:
        f.seek(t["record_offset"]); raw=json.loads(f.read(t["record_length"]))
    if isinstance(raw,dict) and "native_records" in raw:
        hs=[dp.text_hash(dp.public_text(x,source=t["source"])) for x in raw["native_records"]]
        ok += dp.fingerprint(hs)==t["content_hash"]
    else:
        ok += dp.text_hash(dp.public_text(raw,source=t["source"]))==t["content_hash"]
print("content_hash_ok",ok,"of",len(m["tasks"]))
PY

# 5) GPU 可用性（本机实测失败）
nvidia-smi --query-gpu=index,name,memory.free --format=csv,noheader,nounits

# 6) 模型 manifest 与 serving/SFT 依赖
PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import json;from pathlib import Path;v=json.load(open("reproduction/audit/model-manifest.json"));print(len(v["files"]),sum(f["bytes"] for f in v["files"]),all(Path(f["path"]).is_file() and Path(f["path"]).stat().st_size==f["bytes"] for f in v["files"]))'
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -c "import importlib.util as u;print({m:u.find_spec(m) is not None for m in ['torch','transformers','accelerate','peft','fastapi','uvicorn','datasets','vllm','trl']})"
```

## 附录 B：关键代码位置索引

| 主题 | 位置 |
| --- | --- |
| 五类/694 口径 | `RSIFlow/validation_skip.py:10-19,22-45`；`reproduction/configs/validation_744.json`；`rounds_3000_5x600_v4/READY.json` |
| BFCL 零推理检查 | `RSIFlow/tool_validation.py:19-39 native_layout`、`:41-60 check_native` |
| BFCL/ACE 真实评分 | `RSIFlow/tool_validation.py:104 evaluate_tool`（ACE 完整对话约束在 `:125`）、`:62 metric_rows` |
| EvalPlus 隔离 smoke | `reproduction/scripts/evalplus_bwrap_smoke.py:23-28 load_spec`（硬编码 humaneval_plus）、`:90-120 run_smoke` |
| 4 评测器 CPU fixture | `RSIFlow/runtime/scripts/verify_native_report_adapters.py:19-86 verify`（`--specs` 默认旧配置，见 `:90`） |
| 官方命令/指标/隔离分支 | `RSIFlow/runtime/rsiflow/task_meta/reporting.py:161-181 official_command`、`:183-207 parse_official_metrics`、`:236-263` 隔离分支、`:268` 分母校验 |
| EvalPlus pinned/后端 | `RSIFlow/runtime/rsiflow/task_meta/evalplus_isolated.py:30 PINNED_COMMIT`、`:218-220 load_pinned_evalplus`、`:243 validate`、`:285 worker_isolation` |
| 最终评估编排 | `RSIFlow/validate.py:9-145 validate`、`:147-160 main` |
| 外部输入快照（B1） | `RSIFlow/validation_sources.py:5-27 snapshot`（`:19 add(spec['data_path'])`）、`:29-31 verify` |
| 冻结选择/角色约束 | `RSIFlow/evaluation_manifest.py:8-38 select_specs` |
| 有效配置覆盖 | `RSIFlow/task_adapter.py:40-84 _config`（覆盖在 `:56-84`） |
| `--check` 实现 | `RSIFlow/launch_meta.py:496,507-529`；`RSIFlow/launcher.py:16-27` |
| 四卡服务/健康/释放 | `reproduction/scripts/model_service_smoke.py`；`RSIFlow/runtime/rsiflow/task_meta/gpu_phases.py:32-67 stop_services`、`:83-136 start_services`、`:138-152 verify_services`；`RSIFlow/runtime/scripts/start_multidomain_service.py:24,40-43`；`RSIFlow/runtime/rsiflow/task_meta/serve_gpu.py:11-14,31-51,84` |
| SFT 4-rank DDP | `RSIFlow/runtime/scripts/train_four_gpu_phase.py:53`；`train_task_meta_sft.py:224,237,259-316` |
