PASS

# 训练侧 S1 r2 candidate_error 分类修复 scoped review（Qwen）

- 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`
- 范围：仅审查未提交 diff 中 `reproduction/scripts/training_side_smoke.py` 的 `_validate_parent_rows` infra 判定一行，及 `RSIFlow/tests/test_training_side_smoke.py` 两个新增测试。
- 真实证据：`reproduction/logs/training-side-v5-s1-20261117T123014Z/runs/training_side_smoke/parent/train_trajectories.jsonl`（实测读取，见 §三）。
- 性质：只读。未修改任何文件除本报告；未启动实验/SCO/API；未读取凭据。git 以属主身份进程级执行（`setpriv --reuid=10045 --regid=60201 --clear-groups env HOME=/tmp`），未改 trust/全局配置。离线单元测试仅 `/tmp` 临时产物（`TMPDIR=/tmp`、`PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`），非实验。

## 结论

**PASS**：0 Critical，0 Important，1 Minor（信息性）。修复准确保留「模型失败 vs 基础设施」边界，与生产 `_classify_error` 分类法一致；不会错误接受零调用或真正 infra 错误；其余既有门全部保留。

## 一、修复内容与生产合同核对

**diff（唯一功能改动）**：`training_side_smoke.py:701`
- 旧：`if row.get("infrastructure_error") or row.get("execution_error_type"):`（任何 execution_error_type 都按 infra 阻断）
- 新：`if row.get("infrastructure_error") or row.get("execution_error_type") == "infrastructure":`

**生产分类法回源**（`rsiflow_runtime.py:759-774` `_classify_error`）：
| 异常链 | error_type | infrastructure_failure |
| --- | --- | --- |
| RSIFlowBudgetExceeded/_BudgetExhausted | `budget_exhausted` | False |
| TaskInfrastructureError | `infrastructure` | **True** |
| TaskContextBudgetExceeded | `context_budget_exhausted` | False |
| JSON*/Generation* | `parse_error` | False |
| 其它（默认） | `candidate_error` | False |

**生产 infra 统一置位**（均 `error_type="infrastructure"` 且 `infrastructure_failure=True`）：
- transport 失败（failed call 无 response_received）：`rsiflow_runtime.py:940-945`
- 工具调用失败（recorder.calls failed）：`:955-961`
- `environment.evaluate` 抛异常：`:971-975`
- `evaluation.infrastructure_error=True`：`:977-984`

**字段映射**：`pipeline_execution.py:227` `'execution_error_type': result.get('error_type')`，即 row 的 `execution_error_type` 直接承载生产 `error_type`；`infrastructure_error` 承载 `infrastructure_failure`。

**结论**：生产中**所有真正 infra 失败**最终都 `error_type="infrastructure"` 且 `infrastructure_error=True`。新守卫的两个子句（`infrastructure_error` 为真 / `execution_error_type=="infrastructure"`）恰好覆盖这些情形；其余标签（candidate_error/parse_error/budget_exhausted/context_budget_exhausted）均 `infrastructure_error=False` 且 `!="infrastructure"`，不被 infra 门拦截，转而接受其余既有门审查。**边界与生产合同精确一致。**

**budget/context_budget 语义佐证**：`rsiflow_runtime.py:986-995` 对 `context_budget_exhausted` 显式置 `verification.status="completed"`、metrics 清零，并注释「A task exceeding its fixed budget is a completed failure, never a reason to remove its denominator or train on it」。即预算耗尽是**完成的模型失败**，非 infra；新守卫不将其按 infra 阻断，正确。

## 二、其余既有门完整保留（逐行核实 `_validate_parent_rows:689-727`）

infra 门之后仍依次强制：
- `verification.status=="completed"`（:703-705）
- `verification.success` 为 bool（:706-707）
- `model_call_count` 为 int 且 `>=1`（:708-709）→ **零调用必阻断**
- `_validate_transport_calls`（:710，completed + 真实 message）
- 持久 per-task receipt 存在（:711-713）、receipt `round_scope.manifest_hash` 匹配（:714-716）、`state_hash` 存在（:717-718）、receipt transport 数 == `model_call_count`（:719-720）
- coverage 恰为五类各一（:724-725）
- `success is not True` 计入 `failures`（:722-723）

故 candidate_error 即便绕过 infra 门，仍须通过评分完成、>=1 调用、transport、持久 receipt、coverage 全部门；任何缺项仍 `incomplete`。**不会错误接受零调用或缺评分回执。**

## 三、真实输出证据（实测读取）

`runs/training_side_smoke/parent/train_trajectories.jsonl`：
| source | model_call_count | verification.status | infrastructure_error | execution_error_type | error | transport |
| --- | --- | --- | --- | --- | --- | --- |
| 2wiki | 8 | completed | False | candidate_error | FileNotFoundError | 8 |
| hotpotqa | 0 | completed | False | candidate_error | BlockingIOError | 0 |
| deepcoder_taco | 70 / 6 | completed | False | None | None | 70 / 6 |
| envscaler | 40 | completed | False | None | None | 40 |

- **2wiki**：8 次 completed 调用 + verification completed + infra=False + candidate_error → 新代码判为**模型失败**（`completed_with_model_failures`），符合 brief 与生产合同（FileNotFoundError 属候选侧错误，非 TaskInfrastructureError）。
- **hotpotqa**：0 调用 + candidate_error → 通过 infra 门后被 `model_call_count>=1` 门阻断（`No real local model call recorded`），`incomplete`。符合 brief「零调用仍阻断」。BlockingIOError 在生产被归类为 candidate_error（非 infra），smoke 遵循生产分类并以零调用门拦截，正确。
- 旧代码对这两行都会因 `execution_error_type` 非空而误报 `Unexplained infrastructure failure`，把 2wiki 的真实模型失败误判为 infra；新代码纠正此误分类。

## 四、测试核实（真实覆盖根因，非掩盖）

新增两测试（`test_training_side_smoke.py:539-566`）：
- `test_candidate_error_with_completed_calls_is_a_model_failure`：rows 设 `execution_error_type="candidate_error"`、`error=FileNotFoundError`、success=False（verification completed、calls>=1 由 `_build_parent_rows` 提供）→ 断言 `completed_with_model_failures`、`model_failures==5`。
- `test_candidate_error_without_model_calls_still_blocks`：rows 设 candidate_error、`model_call_count=0`、`transport_calls=[]` → 断言 `incomplete` 且 error 含 `No real local model call recorded`。

实跑（含既有边界测试）`4 passed in 3.16s`：
- `test_candidate_error_with_completed_calls_is_a_model_failure` ✓
- `test_candidate_error_without_model_calls_still_blocks` ✓
- `test_infrastructure_and_missing_score_block` ✓（infra=True 仍阻断）
- `test_wrong_answers_are_completed_with_model_failures` ✓

测试与真实 2wiki/hotpotqa 情形一一对应，且保留 infra/缺评分阻断测试，**未掩盖根因**。

## Minor

### M1 — infra 双子句冗余，宜补注释引用生产合同（信息性，非阻塞）

- 证据：`training_side_smoke.py:701` 的 `infrastructure_error` 与 `execution_error_type=="infrastructure"` 两子句在生产中总是同时成立（所有 infra 路径同置 `error_type="infrastructure"`+`infrastructure_failure=True`，见 §一），故第二子句冗余。
- 评价：冗余为**无害的 defense-in-depth**（可捕获假设的 `execution_error_type="infrastructure"` 但 `infrastructure_error` 缺失/False 的不一致 row），非缺陷。`infrastructure_error` 是权威主判据。
- 建议：在该行补一行注释引用 `rsiflow_runtime.py:759-774/940-984` 与 `pipeline_execution.py:227`，说明「仅 TaskInfrastructureError/transport/tool/evaluate 失败为 infra（恒 infrastructure_error=True）；candidate_error/parse_error/budget 为模型侧，须过其余门」，便于后续维护者理解边界来源。非阻塞。

## 剩余限制

- 本审查为静态回源 + 离线单元测试 + 真实轨迹只读读取；**未运行**真实 rollout/SFT/SCO/API。修复后真实 S1 重跑（确认 2wiki 记为模型失败、hotpotqa 零调用阻断、整体 coverage/正例筛选）需另行授权执行以最终确认。
- 仅审查指定一行 diff 与两个新增测试；未重做全量审计。`_validate_parent_rows` 其余门沿用此前已核实结论。
- 行号基于当前未提交工作树；提交后若变动须重核。
- 未读取凭据；git 仅属主进程级 diff，未改 trust/全局配置。

## 建议

- 分类修复正确，可进入主 agent 回源复核与（另行授权的）真实 S1 重跑。
- 可选：M1 补边界来源注释（非阻塞）。
