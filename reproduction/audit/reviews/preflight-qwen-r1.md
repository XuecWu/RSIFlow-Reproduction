# frozen 数据与过程页独立复核（r1）

- 复核 agent：`rsiflow-qwen`（Qwen Code，w1:pV），受主 agent 委派
- 复核时间：2026-11-11 15:52–15:56 UTC（快照时刻；期间主 agent 并行改动 cohort，见问题 2）
- 复核范围：`assets/cohort/build_summary.json`、`RSIFlow/runtime/data/rounds_3000_5x600_v2/READY.json`、`web/data/reproduction.json`、`web/data/process.json`，对照 `RSIFlow/tests/test_600_744_protocol.py`
- 复核边界：只读。未修改任何仓库文件；sqlite 以 `mode=ro` 打开；pytest 使用 `-p no:cacheprovider --basetemp=/tmp/pf_basetemp` 与 `PYTHONDONTWRITEBYTECODE=1`；临时 git config 写在 `/tmp` 并已删除；未启动任何实验、未读取或输出凭据。

## 结论

**CHANGES**

未发现把未执行实验标为完成的情况（详见第四节）。但存在 9 处一致性/可核验性缺陷，其中"744 与 694 口径矛盾"和"协议测试实体断言全部 skip 却被当作协议证据"会直接误导公开站点的读者。

## 一、已独立复核为真的部分

以下数字由本次复核直接重算，可作为已验证事实引用：

- `RSIFlow/runtime/data/rounds_3000_5x600_v2/tasks.sqlite`
  - 3000 行；3000 个唯一 `task_id`；3000 个唯一 `content_hash`。
  - 按 `B1–B5` 目录分桶，每轮 600 条。
  - 每轮 source 组成 `{envscaler 200, deepcoder_taco 200, hotpotqa 100, 2wiki 100}`，与 `train_quotas_per_round` 一致。
  - 每轮 code `interface = {stdio 120, function 80}`（五轮合计 600 stdio / 400 function，与 `build_summary.code_interface` 一致）。
  - 跨轮 `task_id` 重叠 0；跨轮 `content_hash` 重叠 0。
  - 最大 group 占比 1.17%–1.5%，满足 `max_environment_fraction_per_round = 0.1`。
- `RSIFlow/runtime/data/validation_744_v2/manifest.json`
  - 744 条；`by_source = {bfcl_v3 152, acebench 50, humaneval_plus 164, mbpp_plus 178, hotpotqa_dev 100, 2wiki_dev 100}`，与 `READY.json.validation_by_source` 完全一致。
  - 全部 `role = independent_validation`、`purpose = report_only`（评估不回流训练/候选接受）。
  - 与 3000 训练任务的 `task_id` 重叠 0、`content_hash` 重叠 0。
  - `task_id` 集合与 `assets/cohort/validation_source_manifest.json` 相同（该文件在复核窗口内一度被移走，见问题 2）。
- `READY.json.config_hash` 可复现
  - `fingerprint(protocol.json)` = `d7ecfd92659e18f5b7cecc7acfd03c638ceaf8ea40b45bbb8ee44e8631b2bf09`，与 `READY.json.config_hash` 一致。
  - 依据：`RSIFlow/evolution_protocol.py:62-64`（`fingerprint` = sort_keys 规范化 JSON 的 sha256）、`RSIFlow/prepare_600_744.py:110`（冻结同一 dict 为 `protocol.json`）、`:125`（`config_hash = fingerprint(config)`）。
  - 附带说明：`source_training_hash` / `source_validation_hash` 取自 source manifest 内部的 `manifest_hash` 字段（`prepare_600_744.py:123-124`），不是文件字节 sha256。语义合法，但回执未注明，按字节 hash 核对会得到"不匹配"的假警报——本次复核即先踩此坑。
- 检索索引
  - `assets/retrieval-v2/search.sqlite` 字节 sha256 = `21e2cf64cb37ac27dd6bdf9ca88753132eb63fa3d345f419f82656223c896157`，与 `assets/retrieval-v2/search.sqlite.manifest.json` 及 `rounds_3000_5x600_v2/search.sqlite.manifest.json` 一致。
  - 语料 `hotpotqa 4841 + 2wiki 3100`；`label_fields_indexed = false`（无标签泄漏）；`nq_coverage = training_context_pool_only_unmeasured`（诚实标注）；已 symlink 进冻结目录。
  - `data_manifest_hash = 78863d14…` 与 `READY.json.source_training_hash` 一致。

## 二、问题清单

### 问题 1（高）：`build_summary.json` 与 `READY.json`(v2) 已不同步，描述两个不同 release

- 证据：
  - `assets/cohort/build_summary.json` → `ready.config_hash = 1840f3a6145e376d241e677d1b0bb59b3bd30fe329a2adbc5dab4fc2fab2ab06`
  - `RSIFlow/runtime/data/rounds_3000_5x600_v2/READY.json` → `config_hash = d7ecfd92659e18f5b7cecc7acfd03c638ceaf8ea40b45bbb8ee44e8631b2bf09`
  - `build_summary.json` → `ready.source_validation_hash = 927a8606270b8b59036cb94a740426eec76490547815aaee52dbdd76d9278617`
  - `READY.json` → `source_validation_hash = f8ccbf3acbee04ca64366f807b775c18c7f4a65b23855a438c12bd0d0477be6d`
  - 仅 `source_training_hash`（`78863d14…`）两边相同；`rounds`/`tasks_per_round`/`training_tasks`/`validation_tasks`/`validation_by_source`/`status`/`evaluation_schedule` 均相同。
  - `web/data/reproduction.json:73-75` 的 `frozen_data: completed` 只引用 v2 的 `READY.json`。
- 复现依据：`fingerprint(json.load(protocol.json)) == READY.config_hash` 为真，但 `!= build_summary.ready.config_hash`。
- 影响：两份"冻结证据"指向不同 cohort 版本。任何按 `build_summary.json` 校验 v2 冻结数据的人都会得到失败结论；公开站点的 `frozen_data` 证据链实际已断。

### 问题 2（高）：被审计对象已被 v3 取代，公开状态未跟进；cohort 已因 QA 缺陷隔离重建两次

- 证据：
  - `reproduction/configs/train_4b.json:156` → `frozen_data_dir = …/rounds_3000_5x600_v3`
  - `reproduction/configs/train_4b.json:163` → `validation_manifest = …/validation_744_v3/manifest.json`
  - 两个 v3 目录在复核时均已存在。
  - `reproduction/configs/train_4b.json:153` → `data_release = "rounds_3000_5x600"`（仍是 v1 名，与 v3 目录不符）；v2 的 `protocol.json` 内 `data_release` 同样为 v1 名。
  - 隔离目录：`assets/cohort-mixed-qa-20261111T151857Z/`、`assets/cohort-qa-id-missing-20261111T154900Z/`。
  - `reproduction/audit/operations.jsonl` sequence 367：`bash -c 'mv assets/cohort "assets/cohort-qa-…'`。
  - 复核过程中 `assets/cohort/train_source_manifest.json` 一度 `FileNotFoundError`，随后恢复。
- 影响：v2 回执的 `source_training_hash` / `source_validation_hash` 在该窗口内不可验证；`frozen_data: completed` 指向的是已被取代的 release；`data_release` 版本名与实际目录不一致会误导后续 resume 与 8B 复用。

### 问题 3（高）：`RSIFlow/tests/test_600_744_protocol.py` 的实体断言在本机全部 skip，却被列为协议证据

- 证据：
  - 实测 `pytest -q -rs RSIFlow/tests/test_600_744_protocol.py` → `4 passed, 2 skipped`
    - `SKIPPED [1] RSIFlow/tests/test_600_744_protocol.py:92: prepare_600_744.py not run on this host`
    - `SKIPPED [1] RSIFlow/tests/test_600_744_protocol.py:134: real cohort not prepared`
  - 根因：`RSIFlow/launch_meta.py:31` → `DEFAULT_CONFIG = PROJECT_ROOT / "configs/train_4b.json"`；而 `RSIFlow/configs/train_4b.json` 的 `frozen_data_dir`、`validation_manifest`、`source_training_manifest`、`source_validation_manifest` 仍是 `/path/to/...` 占位符，`data_release` 为 `rounds_3000_5x600`(v1)。
  - 因此 `test_600_744_protocol.py:96-115`（每轮 600、source 配额、`{stdio:120, function:80}`、跨轮 id/content 不重复、native loader、`assert_disjoint`、`select_specs` → 744）在本机是死代码；`:136-159`（六 benchmark、`n_scored == 744`）同样 skip。
  - `reproduction/audit/reviews/environment-main.md` 记录的 "160 passed、2 skipped、1 failed" 中的 2 skipped 即这两条。
  - `web/data/repository.json:76` 将 `RSIFlow/tests/test_600_744_protocol.py` 与 `RSIFlow/configs/train_4b.json` 并列为 protocol 证据。
- 影响：公开材料暗示"协议由上游测试保证"，实际上 3000/744/120+80 的验证来自本次复核与临时脚本，而非该测试。任何人 clone 仓库跑测试都得不到这些数据结论。

### 问题 4（高）：744 与 694 在四处记录中互相矛盾

- 证据：
  - `reproduction/configs/train_4b.json:93` → `"skip_acebench": true`（实际会跑的机器配置）
  - `RSIFlow/runtime/data/rounds_3000_5x600_v2/protocol.json` → `"skip_acebench": true`
  - `RSIFlow/configs/train_4b.json:93` → `"skip_acebench": false`，且 `RSIFlow/tests/test_600_744_protocol.py:28` → `assert not runtime.round_protocol['skip_acebench']`（该断言确实运行并通过，但校验的是占位配置）
  - `RSIFlow/runtime/data/rounds_3000_5x600_v2/READY.json` → `validation_tasks: 744`，无任何 skip 字段
  - `web/index.html:59`（`744 最终独立评估`）、`:148`（`六类任务，共 744`）、`:192`、`:195`、`:196`；`web/RESULTS.md:29` → 一律以 744 为最终独立评估，无 694 说明
  - `web/data/reproduction.json:35` → `"ace_mode": "skipped_694_of_744"`（有披露），但同文件 stage id 仍为 `evaluate_4b_744`(:103)、`evaluate_8b_744`(:113)
  - `reproduction/audit/runs.json` 与 `web/data/process.json` 末段 → `"evaluation": "694_of_744_ace_skipped"`
  - `RSIFlow/tests/test_600_744_protocol.py:157-159` → 断言 `benchmarks_completed == 6`、`n_scored == 744`，与 skip-ACE 的 5/694 冲突；因该测试 skip，冲突处于潜伏状态
- 代码侧是诚实的（不需要改）：`RSIFlow/validation_skip.py` 返回 `status="code_search_completed_ace_pending"`、`acebench_status="incomplete_requires_audit"`、`officially_scored=694`、`expected_total=744`、`feedback_to_meta=False`，并有 `reject_complete_cache_for_skip()` 阻止复用完整评估缓存。
- 影响：站点标题数字（744）与配置将实际产出的结果（694）不一致；`READY.json` 单独阅读会得出"将评 744"的错误结论；上游测试把 744/6 写成契约，未来若打开数据断言会与 skip-ACE 直接冲突。

### 问题 5（中）：`frozen_data: completed` 未携带冻结数据自带的保留意见

- 证据：
  - `web/data/reproduction.json:73-75` → `{"id": "frozen_data", "status": "completed", "evidence": "RSIFlow/runtime/data/rounds_3000_5x600_v2/READY.json"}`
  - `RSIFlow/runtime/data/validation_744_v2/manifest.json` → `clean_independence_audited: false`；744 条全部 `exposure: "exposure_unknown"`、`version: "reconstructed-v1"`
  - `assets/cohort/build_summary.json` → `source_audit.limitations`: "No semantic paraphrase guarantee"、"Unlabeled variants may remain undetected"、"No claim about model pretraining contamination"
  - 公开状态仅在 `web/data/reproduction.json:31-35 current_execution.protocol = "protocol_reconstruction"` 间接体现
- 影响：`completed` 会被读作"冻结数据已验证干净"，而实际上独立性审计未完成、暴露状态未知、属同协议重构（AGENTS.md 第 5 节要求此类重构单独审批并明确标注）。

### 问题 6（中）：`build_summary.json` 内部账目不可核对

- 证据：
  - `source_audit.valid_unique_native_ids = 249460`，恰等于全部 `raw_records` 之和：2550 + 7436 + 45224 + 167454 + 5250 + 1023 + 164 + 378 + 7405 + 12576 = 249460
  - `source_audit.valid_unique_ids = 249337`，仅比上值少 123
  - `duplicate_variants_dropped` 合计 1024（`bfcl_v3: 1`、`acebench: 1023`，其余为 0），其中 `acebench: 1023` 等于其全部 `raw_records`
  - 但 744 中确有 50 条 acebench：`wc -l RSIFlow/runtime/data/validation_744_v2/acebench.jsonl` = 50；`wc -l assets/cohort/normalized/validation/acebench.jsonl` = 1023
  - `quarantine_records = 25884` 未与上述任一数字建立可对账关系
- 影响："dropped 1023" 与"保留 50"无法同时成立，字段语义不明；这是 cohort 重建的唯一汇总证据，账目不可核对会削弱问题 1/2 的可追溯性。

### 问题 7（中）：`sco_gpu_probe` 状态低报且缺 evidence 链接

- 证据：
  - `web/data/reproduction.json:78-80` → `{"id": "sco_gpu_probe", "status": "queued", "evidence": null}`
  - `reproduction/audit/runs.json` → `sco_job_id: "pt-r6np9yvd"`、`status: "probe_queued"`
  - `reproduction/audit/operations.jsonl` 中 `sco` 命令多次成功：sequence 281、282、290、307、322、329、377…
  - `pt-r6np9yvd` 出现在 15 个 evidence 日志（00000264/266/271/281/290/307/322/329/337/342/343/350/352/356/358）与 `reproduction/audit/reviews/environment-main.md`
- 影响：方向是低报而非虚报，但公开阶段状态与账本事实不一致，读者无法从站点跳到 probe 证据。

### 问题 8（中）：`web/data/process.json` 是过期快照，却以实时视图呈现

- 证据：
  - `web/data/process.json:3` → `generated_at = 2026-11-11T15:39:42.823731Z`；`:11` → `operations_total = 301`；`:5-10` → `{success 235, failed 61, blocked 1, timeout 4}`；`:12` → `results_total = 21`
  - 复核时 live ledger：`reproduction/audit/operations.jsonl` 348 行、max sequence 384、`{success 277, failed 66, blocked 1, timeout 4}`；`key_results.jsonl` 23 行
  - 差额约 47 次操作，其中含 5 次新增失败
- 影响：公开过程页少报操作与失败，且无"截至时刻/截至 sequence"提示，读者会误以为是实时状态。

### 问题 9（中）：审计证据不可持久、无 hash，公开页面却引用其路径

- 证据：
  - `.gitignore:28` → `*.log`；`git check-ignore -v reproduction/audit/evidence/00000319-stdout.log` → 命中该规则
  - `git ls-files reproduction/audit/evidence | wc -l` → 0
  - `reproduction/audit/operations.jsonl` 348 行中无任何 sha256/hash 字段（上一轮 schema 复核已记录同类问题，路径由 `logs/` 改为 `evidence/` 但仍被 `*.log` 忽略）
  - `web/data/process.json` 的 timeline 每条都引用 `reproduction/audit/evidence/000003NN-{stdout,stderr}.log`
- 影响：站点（cpolar 公开）引用的原始输出证据既不在版本库中、也无内容 hash，可被静默改写或删除而无从发现；`key_results` 有 hash 而 `operations` 没有，两级证据强度不一致。

## 三、次要不一致

- `reproduction/configs/train_4b.json:153` 的 `data_release` 与 `:156` 的 `frozen_data_dir` 版本名不一致（见问题 2）。
- `RSIFlow/configs/train_4b.json` 的 `meta_worker_socket` / `meta_relay_socket` 均写 `rsiflow_8b_*`（4B 配置里出现 8B 名），属上游既有写法；在 `web/data/reproduction.json:16-28 conditional_execution` 主张双流水线隔离时，该命名会加剧端口/socket 冲突风险，建议一并在机器配置中区分。

## 四、关于"是否把未执行实验标为完成"

**未发现伪造的实验完成状态。** 需要 GPU/API 的阶段全部为未执行：

- `web/data/reproduction.json:78` → `sco_gpu_probe: queued`
- `:83` `model_service_smoke`、`:93` `benchmark_smoke`、`:98` `run_4b_five_rounds`、`:103` `evaluate_4b_744`、`:108` `run_8b_five_rounds`、`:113` `evaluate_8b_744` → 均为 `not_run_pending_approval`，`evidence: null`
- `:88` `meta_api_smoke` → `blocked_missing_credentials`
- `reproduction/audit/runs.json` → `run_dir: null`、`started_at: null`、`ended_at: null`、`status: "probe_queued"`
- `web/data/reproduction.json:39` → `skip_ace_744.status = "focused_tests_passed_full_suite_pending"`（明示全量未完成）
- `:46` → `upstream_suite = "160 passed, 2 skipped, 1 failed"`（未隐瞒失败与 skip）；`reproduction/audit/reviews/environment-main.md` 明写"不修改上游断言、不宣称全量测试通过"
- `:8-14`、`:16-28` → 两个执行 profile 均 `verified: false`

5 个 `completed*` 阶段均为非实验项，且证据文件存在：`conda_environment`(:53-55, `web/rsiflow-requirements.lock.txt`)、`external_assets`(:58-60, `reproduction/audit/assets.json` 现有 10 条资产，含 Qwen3-4B 13 文件/8060926626 字节、codex-cli 0.153.4 sha256 `56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da` 与 AGENTS.md provenance 一致)、`offline_tests`(:63-65)、`launch_preflight`(:68-70)、`frozen_data`(:73-75)。

真正的风险不是"假完成"，而是问题 3、4、5：站点以 744 为标题数字、以 `test_600_744_protocol.py` 为协议证据，而该测试的数据断言在本机全部 skip，实际配置只会评 694；同时 `frozen_data: completed` 未带 `clean_independence_audited=false` / `reconstructed-v1` / `exposure_unknown` 的保留意见。

## 五、修复建议（按优先级）

1. **统一 744 / 694 口径（对应问题 4）**
   - 在 `web/index.html:59,148,192,195,196`、`web/RESULTS.md:29` 明确写成"744 冻结 / 694 实际评分（ACEBench 待审计）"。
   - 将 `web/data/reproduction.json:103,113` 的 stage id 或新增字段体现 694（例如 `evaluation_scope: "694_of_744_ace_pending_audit"`），不要只放在 `current_execution.ace_mode`。
   - 在 `READY.json` 生成逻辑（`RSIFlow/prepare_600_744.py:119-127`）中补记 `skip_acebench` 与实际评分任务数，使冻结回执自身不产生"将评 744"的误读。
   - 决定 skip-ACE 是否为已批准偏离：若是，需同步说明 `test_600_744_protocol.py:28,157-159` 的 744/6-benchmark 契约如何处置（不得通过修改上游断言来掩盖，AGENTS.md 第 11 节）。
2. **让协议测试真正跑起来（对应问题 3）**
   - 为 v3 提供一份可被测试选中的真实配置（新增测试专用 config 或环境变量注入，不改上游断言），使 `:92`/`:134` 不再 skip，从而真实覆盖 600/配额/120+80/disjoint/`select_specs`。
   - 在此之前，从 `web/data/repository.json:76` 移除把该测试当作 3000/744 证据的表述，改为引用本次复核结论与 v3 的 `READY.json`。
3. **消除 build_summary 与 READY 的版本错位（对应问题 1、2）**
   - `frozen_data` 证据改指向 v3 的 `READY.json`，并同时给出对应版本的 `build_summary.json`；或显式标注两份文件所属 release。
   - 修正 `data_release` 与 `frozen_data_dir` 的版本名一致性（`reproduction/configs/train_4b.json:153,156`）。
   - cohort 重建/隔离（`assets/cohort-mixed-qa-*`、`assets/cohort-qa-id-missing-*`）应在公开状态中留一行说明，避免"completed"看起来一次成型。
4. **补上冻结数据的保留意见（对应问题 5）**
   - 在 `frozen_data` 阶段附 `clean_independence_audited=false`、`version=reconstructed-v1`、`exposure=exposure_unknown` 与 `build_summary.source_audit.limitations` 三项，状态可改为 `completed_protocol_reconstruction_with_caveats`。
5. **修正 build_summary 账目（对应问题 6）**
   - 重定义 `duplicate_variants_dropped` 语义（是"折叠的 native 变体数"还是"被剔除记录数"），并给出逐源 `raw → quarantine → dropped → valid` 对账表，使 acebench 1023 与保留 50 可同时成立。
6. **更新 probe 状态与过程页新鲜度（对应问题 7、8）**
   - `sco_gpu_probe` 按账本事实更新状态并填 `evidence`（指向含 `pt-r6np9yvd` 的 evidence 日志与 `reviews/environment-main.md`）。
   - `web/data/process.json` 增加 `as_of_sequence` 与生成时刻说明，或改为访问时按需重生成；同时把失败计数与 live ledger 对齐。
7. **让审计证据可持久（对应问题 9）**
   - 为 `operations.jsonl` 的 evidence 增加 sha256（与 `key_results.jsonl` 同强度）。
   - 把 `reproduction/audit/evidence/` 从 `.gitignore:28 *.log` 中例外出来（例如追加 `!reproduction/audit/evidence/` 与 `!reproduction/audit/evidence/*.log`），或改存到未被忽略的证据目录；公开前先跑密钥扫描。

## 六、复核方法与可复现命令

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow

# 1) 冻结 cohort 实体核对（只读 sqlite）：每轮 600、source 配额、120 stdio + 80 function、跨轮不重复
PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
import sqlite3, json, re
from collections import Counter
from pathlib import Path
base = Path("RSIFlow/runtime/data/rounds_3000_5x600_v2")
con = sqlite3.connect("file:%s?mode=ro" % (base / "tasks.sqlite"), uri=True)
rows = list(con.execute("select task_id,domain,source,content_hash,group_key,path,offset,length from tasks"))
rnd = lambda p: re.search(r"/(B[1-5])/", p).group(1)
b = {}
for r in rows: b.setdefault(rnd(r[5]), []).append(r)
for k in sorted(b):
    part = b[k]; iface = Counter()
    for r in part:
        if r[2] == "deepcoder_taco":
            with open(r[5], "rb") as f:
                f.seek(r[6]); rec = json.loads(f.read(r[7]))
            iface[(rec.get("strata") or {}).get("interface") or rec.get("interface")] += 1
    print(k, len(part), dict(Counter(r[2] for r in part)), dict(iface))
print("TOTAL", len(rows), "uniq_id", len(set(r[0] for r in rows)), "uniq_content", len(set(r[3] for r in rows)))
PY

# 2) config_hash 可复现性
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=RSIFlow:RSIFlow/runtime python3 -B -c \
 'import json;from evolution_protocol import fingerprint;print(fingerprint(json.load(open("RSIFlow/runtime/data/rounds_3000_5x600_v2/protocol.json"))))'

# 3) 协议测试实际是 4 passed / 2 skipped（数据断言未执行）
PYTHONDONTWRITEBYTECODE=1 /data/wuxuecheng/miniforge3/envs/rsiflow-offline/bin/python -m pytest -q \
  -p no:cacheprovider --basetemp=/tmp/pf_basetemp -rs RSIFlow/tests/test_600_744_protocol.py

# 4) 744 组成与 disjoint
PYTHONDONTWRITEBYTECODE=1 python3 -B -c \
 'import json;from collections import Counter;m=json.load(open("RSIFlow/runtime/data/validation_744_v2/manifest.json"));print(len(m["tasks"]),dict(Counter(t["source"] for t in m["tasks"])),m["clean_independence_audited"])'

# 5) 证据是否被 git 忽略
git check-ignore -v reproduction/audit/evidence/00000319-stdout.log
git ls-files reproduction/audit/evidence | wc -l

# 6) 公开过程页与 live ledger 的差异
wc -l reproduction/audit/operations.jsonl reproduction/audit/key_results.jsonl
PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import json;p=json.load(open("web/data/process.json"));print(p["generated_at"],p["operations_total"],p["operation_counts"],p["results_total"])'
```

## 七、状态

`CHANGES` — 数据实体（3000 / 5×600 / 每轮 120 STDIO + 80 function / 744 组成 / disjoint / 检索索引 hash / config_hash 可复现）全部复核通过；但公开状态与冻结回执存在 9 处一致性、可核验性与证据持久性缺陷，其中问题 1–4 为高优先级，需在下一轮修正后重新复核。
