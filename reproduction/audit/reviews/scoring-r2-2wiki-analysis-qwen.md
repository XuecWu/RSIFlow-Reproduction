CHANGES

# 真实评分 r2 2Wiki 官方评测失败分析（Qwen，只读）

- 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`
- 真实输出：`reproduction/logs/model-scoring-v5-a100-20261117T122959Z`
- 性质：只读分析，未改任何代码/配置/数据；未启动实验/SCO/API；未读取凭据。仅写本报告。git 以属主身份进程级执行（`setpriv --reuid=10045 --regid=60201 --clear-groups env HOME=/tmp`），未改 trust/全局配置。

## 结论

**CHANGES**：0 Critical，1 Important，2 Minor。2Wiki 官方评测失败是**gold 文件物化层缺少官方字段解析**的真实缺陷，**同时影响 smoke 与生产 694**。最小修复应在**生产公共适配层 `evaluation_manifest.select_specs`** 补齐与 `prepare_report_specs.py` 一致的官方字段解析，并让 smoke subset 复用同一逻辑；保留 frozen bytes/hash 与分母，不改第三方官方脚本，不据模型结果换题。

## 一、根因与准确证据

### 1.1 失败现象（真实 stderr）

`scores/2wiki_dev/stderr.txt`：
```
File ".../third_party/report_evaluators/2wiki/2wikimultihop_evaluate.py", line 114, in normalize_evi
    evidences[idx_1][idx_2] = white_space_fix((remove_punc(lower(evidences[idx_1][idx_2]))))
TypeError: 'str' object does not support item assignment
```
调用链：`eval(sys.argv[1], sys.argv[2])`（:203）→ `update_evi(..., dp['evidences'])`（:178）→ `normalize_evi(gold)`（:120）→ 对 `dp['evidences']` 做嵌套 item assignment（:112-114）。官方脚本期望 `evidences` 为**嵌套数组**，实际拿到 **JSON 字符串**，`evidences[idx_1]` 取到字符（str），赋值即 TypeError。

### 1.2 字段类型证据（实测）

- smoke `subset/2wiki_dev.json`（list, len=1）：`context/supporting_facts/evidences` 均为 **str**（JSON 编码字符串），`question/answer/_id/type` 为 str。
- **原始 frozen v5 pack**（`validation_744_v5/2wiki_dev.jsonl`，按 manifest offset/length 读回）：`context/supporting_facts/evidences` 同样为 **str**。
  → **字符串字段源自 frozen 数据本身，非 smoke 篡改。** smoke `build_plan_artifacts`（`model_scoring_smoke.py:162-170`）仅 `read_task_record`（逐字读 frozen）+ `json.dumps([record])`，忠实保留 frozen 内容。
- 对照 hotpotqa frozen：`context/supporting_facts` 为 **dict**（非 str）。hotpotqa 官方脚本容忍 dict 格式，故 smoke 中 hotpotqa 已 scored；2Wiki 官方脚本要求嵌套数组，字符串格式不被容忍 → 仅 2Wiki 失败。

### 1.3 官方字段解析逻辑**已存在**于 prepare_report_specs.py

`RSIFlow/runtime/scripts/prepare_report_specs.py:49-57`（searchqa gold 转换）：
```python
for key in ('context', 'supporting_facts', 'evidences'):
    value = row.get(key)
    if isinstance(value, str):
        row[key] = json.loads(value)          # JSON 字符串 → 解析数组
if isinstance(row['supporting_facts'], dict):
    sf = row['supporting_facts']
    row['supporting_facts'] = list(zip(sf['title'], sf['sent_id'], strict=True))  # dict → tuples
```
转换后写 `<identifier>_official.json`（:59-60）并注册为 official_specs 的 data_path。**这是官方评测器要求的规范 gold 格式。**

### 1.4 缺陷：select_specs 与 smoke subset 重建 gold 时**绕过**了该解析

- **生产 694 路径**：`validate.py:55-56` `select_specs(settings, manifest_path, role, out/'inputs', ...)`；`evaluation_manifest.py:28-39` 逐字读 frozen record（`:32 raw.append(json.loads(f.read(...)))`）后 `json.dumps(raw)` 写 `data=out/(name+'.json')`（`:33-34`），**未做任何字段解析**；`:39` 用该 raw gold **覆盖** official_specs 中已解析的 data_path。`validate.py:86` `specs=selected_specs or ...`（selected_specs 非空 → 用 raw gold）；`:119 evaluate_official` → `reporting.py:164 official_command` 把 `spec.data_path`（字符串字段 gold）直接传官方脚本 → 同样 TypeError。
- **smoke 路径**：`model_scoring_smoke.py:162-170` 同样逐字 + `json.dumps`，无解析 → 失败。

→ 两条路径都从 frozen 重建 gold 却省略了 `prepare_report_specs.py:49-57` 的解析；prepare_report_specs 预解析的 official gold 在 select_specs 阶段被 raw gold 覆盖而失效。

## 二、生产 694 是否受影响：**是**

证据链：`validate.py:55-56`（select_specs）→ `evaluation_manifest.py:32-34`（raw gold，无解析）→ `validate.py:86`（采用 selected_specs）→ `:119 evaluate_official` → `reporting.py:164`（官方脚本读 spec.data_path）→ `2wikimultihop_evaluate.py:114` TypeError。

故生产 694 的 2Wiki 官方评分会以**完全相同**的 TypeError 失败（fails closed，不产生错误分数，但该 benchmark 无法 scored）。其余 benchmark：HumanEval+/MBPP+ 走 EvalPlus（不依赖这些 QA 字段）、BFCL 走 native、HotpotQA 字段为 dict 被其官方脚本容忍，故仅 2Wiki 受阻。**这不是 smoke 独有问题，仅修 smoke 会遗留生产 694 缺陷。**

## 三、最小修复方案（推荐：生产公共适配层统一处理）

### 3.1 方案比较

| 方案 | 位置 | 评价 |
| --- | --- | --- |
| A 仅 smoke subset 解析 | `model_scoring_smoke.py:162-170` | 修好 smoke，但**生产 694 仍坏**；且与生产逻辑分叉，重复实现 |
| **B 生产公共适配层统一（推荐）** | `evaluation_manifest.select_specs`（:32-34）补解析；smoke 复用同一 helper | 一处修复同时覆盖生产 694 与 smoke；与 prepare_report_specs 规范格式一致；单一事实来源 |
| C 改 frozen 数据 normalization | build_cohort/prepare_600_744 | **改变 frozen bytes/hash**，违反约束，需新 frozen release；非最小 |
| D 改第三方官方脚本 | 2wikimultihop_evaluate.py | **禁止**：掩盖错误、破坏 pinned 官方评测器 |

**推荐 B**：把 `prepare_report_specs.py:49-57` 的官方字段解析抽为共享 helper（如 `normalize_official_qa_record(row)`），在 `select_specs` 物化 gold 时调用；smoke `build_plan_artifacts` 复用同一 helper（或干脆调用 select_specs 生成 subset gold）。这样：
- **保留 frozen bytes/hash**：只转换**派生 gold 文件**（select_specs 输出 / smoke subset），frozen pack 逐字不动，`record_sha256`/`manifest_hash` 不变。
- **保留分母**：同一批 task、同一 ids，仅字段表示从 JSON 字符串变为解析数组；denominator 不变。
- **不改第三方官方脚本**、**不据模型结果换题**：解析是确定性的格式归一，与模型输出无关，选择规则不变。
- 派生 gold 的 `data_sha256` 会变化（属派生产物，预期）；smoke 需以新 plan root 重新物化。

### 3.2 需要修改的文件（建议，未实施）

1. `RSIFlow/evaluation_manifest.py` — `select_specs`（:28-39）：物化 QA gold 时对每条 record 应用官方字段解析（JSON-string→数组；dict supporting_facts→tuples），与 prepare_report_specs.py:49-57 一致。
2. `reproduction/scripts/model_scoring_smoke.py` — `build_plan_artifacts`（:162-170）：QA subset gold 复用同一解析 helper（避免与生产分叉）。
3. （可选重构）`RSIFlow/runtime/scripts/prepare_report_specs.py` — 把 :49-57 解析抽为共享 helper，消除三处重复。
4. 测试：`RSIFlow/tests/test_600_744_protocol.py`（或新增 select_specs gold 格式测试）与 `RSIFlow/tests/test_model_scoring_smoke.py`（subset gold 格式）。

### 3.3 RED 测试（先失败再修）

- **select_specs gold 格式**：fixture frozen 2wiki record 的 `context/supporting_facts/evidences` 为 JSON 字符串；调用 `select_specs`；断言派生 gold 中这些字段为**解析后的 list**（非 str），且 `supporting_facts` 为 [title, sent_id] 序列。RED：当前 select_specs 产出 str。
- **官方评测器可运行**：用物化后的 gold + 一条 dummy prediction 调官方 `2wikimultihop_evaluate.eval`（或 `normalize_evi`/`normalize_sp`），断言**不抛 TypeError** 且按固定分母输出 metrics。RED：当前 raw gold 触发 `normalize_evi` TypeError（真实 stderr 复现）。
- **smoke subset 格式**：fixture frozen 2wiki 字符串字段；`build_plan_artifacts`；断言 `subset/2wiki_dev.json` 字段为解析数组。RED：当前为 str。
- **frozen 不可变 + 分母守恒**：物化前后 frozen pack `file_hash` 不变；task ids/denominator 不变；仅派生 gold 变化。
- **hotpotqa 回归**： normalization 后 hotpotqa gold（dict→tuples）仍能被其官方脚本 scored（确保统一解析不破坏已 scored 项）。
- **生产 694 路径回归**：模拟 validate.py 经 select_specs 的 2wiki gold，断言官方评测可运行（锁定生产缺陷被修复）。

## Minor

### M1 — hotpotqa 与 2wiki frozen 字段表示不一致（dict vs JSON-string）

- 证据：hotpotqa frozen `context/supporting_facts` 为 dict；2wiki 为 JSON-string（§1.2）。两者官方脚本容忍度不同，导致仅 2wiki 失败。
- 影响：统一解析 helper 须同时处理「JSON-string→数组」与「dict supporting_facts→tuples」两种归一（prepare_report_specs.py:51-57 已涵盖），不能只处理 2wiki 字符串而遗漏 hotpotqa dict，否则与规范 official gold 格式不一致。非阻塞，但修复须覆盖两源。

### M2 — select_specs 覆盖 official_specs 已解析 data_path 的设计宜注释说明

- 证据：`evaluation_manifest.py:39` 用 raw 派生 gold 覆盖 `official_specs` 中 prepare_report_specs 预解析的 data_path；`validate.py:86` 优先 selected_specs。
- 影响：这是「预解析 official gold 被运行时 raw gold 覆盖」的根源。修复时宜在 select_specs 补注释，说明派生 gold 必须与 prepare_report_specs 规范格式一致，避免未来再次漂移。非阻塞。

## 剩余限制

- 本分析为静态回源 + 真实输出只读读取；**未运行**官方评测/实验/SCO/API。修复后需以真实 2wiki gold 重跑官方评测确认 TypeError 消除、分母守恒、hotpotqa 仍 scored（另行授权）。
- 未逐行通读官方 2wiki/hotpotqa 脚本全文，仅核对失败点（normalize_evi/normalize_sp/update_evi）与 gold 加载（`json.load` 整文件数组）；hotpotqa「dict 被容忍」基于真实 scored 事实推断，修复后须回归验证。
- 行号基于当前工作树（含未提交 diff）；代码变动后须重核。
- 未读取凭据；git 仅属主进程级，未改 trust/全局配置。

## 建议

- 采用方案 B：在 `evaluation_manifest.select_specs` 补齐官方字段解析（共享 helper），smoke 复用；先写 §3.3 RED 测试再最小实现。
- 该修复同时关闭 smoke 与生产 694 的 2Wiki 阻塞；保留 frozen bytes/hash 与分母；不改第三方官方脚本、不换题。
- 修复后真实重跑（smoke 与生产 694 的 2wiki 官方评测）需单独授权。
