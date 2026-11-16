# 训练侧最小真实 smoke 实施报告

结论：**runner 与离线回归已实现并 GREEN，真实资产 `plan` 已生成并独立复核；真实 GPU/SCO 阶段（S1 rollout / S2 SFT / S3 recovery）未执行，需单独授权。不宣称五轮协议通过、不宣称模型提升、不产生 benchmark 分数。**

- 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`（分支 `reproduction/4b-preflight`）
- 依据：`reproduction/audit/reviews/training-side-smoke-brief.md`、`training-side-smoke-design.md`（推荐五条 mini-B1 方案）
- 未 commit/push，未读取凭据，未启动真实模型/API/SCO/五轮/SFT，未改生产 `TaskAdapter`/pipeline/trainer/gpu phases/领域 adapter/正式配置/frozen assets。

## 1. 交付物

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `reproduction/scripts/training_side_smoke.py` | 新建（tracked） | 单一 runner，679 行；5 个子命令 + 编排/验收 helper，不复制生产算法 |
| `RSIFlow/tests/test_training_side_smoke.py` | 新建（tracked） | 初版 48 项；fix round 1 重写为 **59 项**生产合同测试（含 6 mutation + 5 legacy-contract RED）；临时 fixture + mock |
| `reproduction/REPRODUCTION.md` | 修改（tracked） | 新增 §10：子命令、阶段门、验收判据、真实 plan 结果与边界 |
| `reproduction/audit/reviews/training-side-smoke-report.md` | 新建 | 本报告 |
| `reproduction/logs/training-side-smoke-plan-r1/` | 新建（Git 忽略 `**/logs/`） | 真实资产离线 plan：selection / smoke launcher / effective config / base manifest / mini release / plan receipt |

## 2. 设计与复用边界

- **原样复用生产接口**：`TaskAdapter.bootstrap()`、`TaskAdapter.rollout(stage=parent)`、`TaskAdapter.sft()`、`TaskAdapter._config()`；`prepare_600_744.repack()`、`data_protocol.native_index()`、`evolution_protocol.make_manifest/freeze/training_quotas`；`gpu_phases.ensure_services/verify_services/stop_services/registry_path`；`sft.select_positive_rows(profile="multidomain")`；`task_client.LocalTaskClient`；`storage.checkpoint_manifest`。
- **不调用** `pipeline.run()`/Meta backend/候选接受/正式 round complete；不新增领域评分器或训练器。
- 服务生命周期由 runner 管理（`ensure_services` 在 rollout 前、`finally` 中 `cleanup_owned_services`），rollout 以 `ensure_services=False` 交给原 `TaskAdapter.rollout` 执行真实 parent pass，避免重复启动；SFT 内部仍由生产 `train_four_gpu_phase` 负责停 base→四 rank DDP→merge→重启 child。
- `config.output_root` 即 smoke 新根；SFT output 位于其 `runs/` 下，merged child 只在该树内；源 checkpoint、v4/v5 release、正式 `Rollout_logs`/`Meta_logs` 全程只读。

## 3. TDD：RED → GREEN

### 3.1 RED（先写测试并实际运行）

```text
RSIFlow/tests/test_training_side_smoke.py:23: in <module>
    import training_side_smoke as smoke
E   ModuleNotFoundError: No module named 'training_side_smoke'
1 error in 0.44s
```

实现过程中逐段暴露并修正的真实缺陷（均先观察到失败再修）：

1. `derive_smoke_config` 最初硬编码 `ROOT/"RSIFlow"` 为项目根，导致 fixture 读到**占位 base**（`task_checkpoint=/path/to/models/Qwen3-4B`），`checkpoint_manifest` 抛 "must contain nonempty weight files"。改为显式 `project_root` 参数并写入 plan receipt，execute 阶段从 receipt 取回。
2. `checkpoint_manifest` 返回 **list[dict]**（`{path,bytes,sha256}`），最初按 dict `.values()` 处理 → 改为按 `sha256` 集合比较 base/child weights。
3. 测试工厂 lambda 通过模块全局名 `_parent_rows` 自引用，monkeypatch 后**递归调用自身** → 拆分为不可 patch 的 `_build_parent_rows` 与可 patch 别名 `_parent_rows`，工厂转发 `**kwargs`。
4. FakeAdapter 最初用合成 task_id（`envscaler:task-0`），被 runner 的"必须属于冻结 mini B1"校验**正确拒绝** → 改为从真实 `plan/selection.json` 与 mini manifest 取 task_id/manifest_hash。
5. 零正例分支最初在 `try` 内提前 `return _finish(...)`，与 `finally` 的 cleanup 叠加导致**重复 cleanup + freeze 覆盖冲突** → 改为设置状态后统一走 `finally` 与一次 `_finish`。
6. `status()` 最初把 `started` 原样返回，与"部分 started 无 final → requires_audit"语义不符 → 映射为 `requires_audit`。

### 3.2 GREEN

```text
RSIFlow/tests/test_training_side_smoke.py                 48 passed in 2.34s
相关 RSIFlow（training_side_smoke / task_adapter / service_startup /
             model_scoring_smoke / 600_744_protocol / release_integration）
                                                          144 passed, 2 skipped in 34.38s
reproduction/tests（全套）                                66 passed in 50.79s
```

2 个 skip 为既有环境性跳过，非本次引入。完整 RSIFlow 全量套件由主 agent 负责复验，本报告不冒充其结果。

### 3.3 Mutation 验证（守卫载荷性）

在**临时副本**上做文本变异并以独立模块名导入；真实 runner、生产代码、frozen assets 与任何真实输出根**从未被修改**，故无需"恢复"。为避免 runner 身份哈希差异掩盖目标守卫，变异模块的 `__file__` 指回真实 runner 路径（仅影响 binding 哈希，不改变被变异代码）。

| 变异 | 关闭的守卫 | 真实 runner | 变异 runner（RED 证据） |
| --- | --- | --- | --- |
| `MUT_ZERO_POSITIVE` | `if not positives:` 零正例阻断 | `blocked_no_positive_parent_trajectory`，零 trainer 调用 | **调用 `TaskAdapter.sft`** 并产出 child |
| `MUT_MANIFEST_BINDING` | rollout 行 `manifest_hash` 绑定 | 错误 manifest 行 → `incomplete` | **接受**错误 manifest 行 → `completed` |
| `MUT_CLEANUP_OWNERSHIP` | `if not owned:` registry 归属 | 拒绝停止外部 registry（`registry_owned=False`+error） | **停止外部 registry** |
| `MUT_WORLD_SIZE` | `world_size==4 且 ranks=={0,1,2,3}` | 单 rank 证据 → `incomplete` | **接受**单 rank → `completed` |
| `MUT_SFT_GATE` | SFT 前必须完成 rollout | 无 rollout → `SmokeBlocked` | **跳过** rollout 前置门 |

过程如实记录：两个 mutation 测试最初因真实 runner 已写入 final receipt 而使变异模块返回 `already_completed`（未触达目标守卫）→ 在调用变异模块前移除对应 final receipt，使变异在目标守卫处被观察到。

## 4. 设计 §7 覆盖对照

> **更正（fix round 1）**：本节初版表格基于与生产合同**不一致**的旧夹具（SimpleNamespace 配置、自造 `trajectory_path`、旧 trainer/GPU 字段名、`choices` 客户端响应），因此当时"真实调用链、恢复、并发及清理合同已验证"的表述**过强、不成立**。Codex scoped review 判定 Spec FAIL。fix round 1 已用生产合同重写夹具与验收，并以 legacy-contract 变异证明新测试确实捕获旧缺陷；**修正后的覆盖与关闭证据见 §8，本节表格仅作历史留档，不再作为当前覆盖结论**。

| 设计要求 | 覆盖测试 |
| --- | --- |
| 1 确定性与覆盖 | `test_selection_is_deterministic_and_covers_five_distinct_kinds`、`test_selection_records_full_record_provenance`、`test_missing_source_fails_closed`（3 参数）、`test_missing_stdio_or_function_interface_fails_closed`、`test_duplicate_task_id_fails_closed`、`test_wrong_role_or_round_fails_closed`、`test_empty_or_tampered_record_fails_closed` |
| 2 mini release | `test_mini_release_preserves_record_bytes_and_contains_only_five`、`test_mini_release_refuses_existing_or_symlinked_output`、`test_original_b1_and_retrieval_hashes_unchanged` |
| 3 有效配置 | `test_effective_config_is_smoke_sized_and_isolated`、`test_sft_preset_is_not_overridden_by_smoke` |
| 4 阶段门 | `test_rollout_requires_plan`、`test_sft_requires_completed_rollout`、`test_recovery_requires_completed_sft`、`test_started_without_final_requires_audit`、`test_tampered_plan_binding_is_rejected`、`test_completed_stage_is_not_rerun`、`test_output_root_must_be_new_and_inside_project` |
| 5 答错 vs infra | `test_wrong_answers_are_completed_with_model_failures`、`test_infrastructure_error_blocks_rollout`、`test_missing_scoring_receipt_blocks_rollout`、`test_rollout_call_chain_and_coverage` |
| 6 零正例 | `test_zero_positives_blocks_sft_without_calling_trainer` + `MUT_ZERO_POSITIVE` |
| 7 正例调用链 | `test_positive_sft_call_chain_and_evidence`（bootstrap→ensure→rollout→positive filter→sft→verify→短生成→stop）、`test_rollout_call_chain_and_coverage` |
| 8 SFT 证据防篡改 | `test_sft_evidence_tampering_fails`（world_size / missing_rank / epochs / base_changed / child_equals_base / weights_unchanged / restored_base / non_finite_loss 共 8 参数）+ `MUT_WORLD_SIZE` |
| 9 清理 | `test_cleanup_failure_fails_overall_even_after_success`、`test_rollout_exception_still_cleans_and_preserves_error`、`test_cleanup_only_targets_the_own_output_root_registry`、`test_no_owned_registry_is_recorded_not_foreign_stop` + `MUT_CLEANUP_OWNERSHIP` |
| 10 恢复不重训 | `test_recovery_never_calls_sft_and_keeps_training_hashes`、`test_recovery_detects_tampered_training_receipt` |
| 11 超时/边界 | 非有限 metrics（`non_finite_loss`）、越界 child path 与 child=base（`child_equals_base`）、base 篡改（`base_changed`）；超时由生产 `training_timeout_seconds`/`task_timeout` 承载，runner 不将 timeout 记为模型失败（infra/exception → incomplete） |
| 12 plan 无副作用 | `test_plan_has_no_model_or_service_side_effects`（ensure/start/stop/verify/LocalTaskClient 全部 patch 为调用即失败，plan 仍只写计划与 hashes，`model_calls=0`） |

## 5. 真实资产离线 plan（无 GPU/模型）

命令（输出根写入前 `test ! -e` 确认不存在）：

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python reproduction/scripts/training_side_smoke.py plan \
  --output-root "$PWD/reproduction/logs/training-side-smoke-plan-r1" \
  --machine-config "$PWD/reproduction/configs/train_4b.json" \
  --parent-manifest /data/wuxuecheng/Hierarchical_level/RSI-Flow/RSIFlow/runtime/data/rounds_3000_5x600_v4/B1/manifest.json
```

stdout：`{"command":"plan","ok":true,"status":"planned","output_root":".../training-side-smoke-plan-r1"}`

回执要点（`receipts/plan.json`，SHA256 `5eb61a709e008e2c5ea6536aa56481c8ccb3e343d7262652fdfbe48d917c250c`）：

- `status=planned`、`model_calls=0`、`selected_tasks=5`；`smoke_only=true`、`formal_round_completed=false`、`eligible_for_meta=false`、`eligible_for_candidate_acceptance=false`。
- `output_root_validation`：`inside_project=true`、`preexisting=false`、`git_ignored=true`、`verification=structural_gitignore_ancestor`（git 因跨 uid 属主不可用，走保守 `.gitignore` 祖先规则；未改任何 git trust）。
- 原 B1 `manifest_hash = 58c54c3f3262e5a8979d0ac051f9f6192e4c8e2b2bcc7b82ae042cb039840caa`；mini `manifest_hash = 3e37d37bc612ccc42a6d2bfabee7e484a93af8854aeb81b1292de19e3cfee13c`；`source_assets_unchanged=true`。
- 固定五条（原 B1 顺序，每类第一条）：
  - `envscaler:env_154_rl:env_154_rl-task_47`
  - `deepcoder_taco:deepcoder-taco-424`（STDIO）
  - `deepcoder_taco:deepcoder-taco-2865`（function）
  - `hotpotqa:5abaf8be5542992ccd8e7e85`
  - `2wiki:f96fae0a0bae11ebab90acde48001122`
- selection SHA256 `e4284be567c14735eb7f314763e543797a6a3d212d6c3d5790d6e0d9568cfbff`；mini manifest 文件 SHA256 `48489280a1cf7a9543300ad69264dd62e19676c3dea35a968f7495f035f4b531`。
- effective config：`window_quotas={tool_use:1, code:2, searchqa:2}`、`max_generations=1`、`rollouts_per_task=1`；SFT preset 完整保留（`num_train_epochs=1.0`、`max_steps=-1`、`learning_rate=2e-6`、`lora_rank=8`、`lora_alpha=16`、`lora_dropout=0.05`、七个 target modules、`seed=42`、`max_length=16384`）。
- `task_checkpoint` 为真实 `assets/models/Qwen3-4B`（base manifest 3 个分片）；四 replica GPU 0/1/2/3 → `127.0.0.1:8271..8274/v1`；`data_dir`/`output_root` 均指向 smoke 新根，与正式 run 隔离。
- **独立复核**：mini release 五条记录按原 offset/length 读回，与原 v4 B1 的 bytes/content_hash/group_id **5/5 完全一致**；`tasks.sqlite` 只含 5 条 `evolve_train`（envscaler 1 / deepcoder_taco 2 / hotpotqa 1 / 2wiki 1）；`search.sqlite` 为指向真实 `assets/retrieval-v3/search.sqlite` 的 symlink；原 B1 pack 与检索索引哈希未变。

## 6. 自审

- **范围**：只新建/修改 brief 点名的 runner、测试、`REPRODUCTION.md` 与本报告；未改生产 `TaskAdapter`/pipeline/trainer/gpu phases/领域 adapter、正式配置、frozen assets、JSONL 账本。mtime 核对：`prepare_600_744.py`、`gpu_phases.py`、`updaters.py`、`train_four_gpu_phase.py`、`train_4b.json` 均为 16:33（工作树创建时间）未动；`task_adapter.py` 的 16:39 来自**先前 config-fix 任务**（`max_wall_seconds`/socket 传递），非本任务改动。
- **不伪造证据**：所有测试用临时 fixture + mock，合成回执明确为 test-only；真实 plan 只产生计划与 hashes，`model_calls=0`；未生成任何真实 rollout/SFT/recovery 证据。
- **零正例红线**：无自然正例即 `blocked_no_positive_parent_trajectory`，不调用 trainer、不写 child、不换题/造标签/借 held-out；已由行为测试与 mutation 双重证明。
- **答错 vs infra**：答错且评分完成 = `completed_with_model_failures`（有效）；缺评分/infra/错误 manifest/越界 = incomplete，绝不记模型零分之外的特殊"通过"。
- **不可变与恢复**：输出根必须新且 Git 忽略；stage lock 禁并发；started-无-final → requires_audit；final 已存在 → already_completed；recovery 零次 SFT 且训练 receipt 哈希不变。
- **owned cleanup**：只停本输出根 registry，无 registry 时记录而非停外部服务；cleanup 失败则总结果失败；GPU 0–3 释放 ≥40,000 MiB 由生产 `stop_services` 强制。
- **口径**：每个 receipt 含 `smoke_only/formal_round_completed=false/eligible_for_meta=false/eligible_for_candidate_acceptance=false`；`same_round_full_B_parent_child_v2` 仅表示完整五条 mini B1，不宣称正式 600-task B1 或五轮。
- **权限**：git 跨 uid 不可用时只读回退，未改 trust/全局配置；`git diff --check` 与 RSIFlow 全量套件留待属主/主 agent 复验。
- **秘密/空白**：runner、测试、手册秘密形态扫描 0 命中，行尾空白/末尾换行 0 问题。

## 7. 遗留与下一步（需单独授权）

1. **S1 rollout（SCO）**：四 base 服务 + 五条真实 parent rollout/评分/回执 + 停服务；五条 coverage/scoring 完整且 cleanup PASS 才进入 S2。
2. **S2 SFT（SCO）**：重启 base 四服务 → 生产 SFT（停 base→四 rank DDP→merge→启动 child→verify+短生成→停 child）；若零正例则 blocked，总 smoke 不 PASS；trainer failure 须确认恢复 base、不发布 child。
3. **S3 recovery（SCO）**：从持久 child state 恢复四服务、校验训练 hashes 未变、短生成、最终停止；不重训。
4. 真实执行前须按设计 §8/§9 检查输出根、stage lock、owned registry、GPU 状态与超时预算；外部 SIGKILL 可能绕过 `finally`，下次启动必须先按 registry PID identity 做 owned cleanup，无法确认 owner 时停止并请求人工核查。
5. 即使 S1–S3 全 PASS，仍不能关闭设计 §12 列出的正式五轮、候选接受/Meta、完整 600 并发、大规模 SFT 质量、五类 final benchmark/BFCL 152/694/ACE、正式 preflight 与长跑审批等结论。
6. `git diff --check` 与 RSIFlow 全量套件由主 agent/属主用户最终复验。

## 8. Fix round 1（回应 `training-side-smoke-review.md` 首轮 Spec FAIL）

> **后续更正**：fix round 1 的 scoped re-review 判定总体 CHANGES——原 10 项中 #3/#6/#9/#10 仍 NOT ADDRESSED，并新增 1 项纯 tool_calls Important。因此本节"10 项全关闭"的措辞**过强、不成立**；当时信号测试只在 mock ensure 中发信号、registry 归属只信自述，不能证明训练中断与真实 PID 归属安全。这些边界在 **§9（fix round 2）** 才以真实子进程/`/proc` 证据关闭。本节保留作历史留档。

**承认**：初版测试夹具与生产合同不一致（SimpleNamespace 配置、自造 `trajectory_path`、旧 trainer/GPU 字段、`choices` 客户端响应），故初版"真实调用链/恢复/并发/清理已验证"表述过强。本轮按生产合同重写 runner 验收与测试夹具，并用 **legacy-contract 变异**（把每项修复回退为旧行为）证明新测试确实捕获旧缺陷——这是本轮的 RED 证据，而非旧 mock 伪绿。

### 8.1 逐项关闭证据

| # | Important | 修复 | 生产合同依据 | 关闭测试 / RED 证据 |
| --- | --- | --- | --- | --- |
| 1 | 配置须为真实 PipelineConfig，`start_services` 可 `model_dump` | `load_effective_config` 改为 `PipelineConfig.model_validate(...)` | `gpu_phases.start_services:104` 调 `config.model_dump()` | `test_effective_config_is_real_pipeline_config_with_model_dump`；legacy `LEGACY_CONFIG`→SimpleNamespace 无 `model_dump`（`test_legacy_config_contract_is_rejected`） |
| 2 | 按真实持久回执验证，不要求 `trajectory_path` | 删除 `trajectory_path` 要求；改验 `transport_calls`（status=completed + response.message）、`model_call_count`、`rollout_cache/*/train_rollouts/*.json` 持久 `{binding,row}` 与 `round_scope.manifest_hash`/`state_hash` | `pipeline_execution._one:265` `save_json(path,{binding,row})`；`scoped_rollout` 落 `rollout_cache/<fp>/train_rollouts`；行含 `transport_calls`/`model_call_count`，无 `trajectory_path` | `test_rollout_accepts_production_rows_without_trajectory_path`、`test_missing_persisted_receipt_blocks`、`test_incomplete_transport_call_blocks`；legacy `LEGACY_TRAJECTORY` 重新要求该字段→生产行被拒（`test_legacy_trajectory_requirement_rejects_production_rows`） |
| 3 | 对齐生产 trainer/GPU 字段；Harness 允许 clone 副本但核验 identity/hash | 改读 `rank_records`(rank/sampled_indices)、`num_train_epochs_completed`、`training_loss`、`changed_lora_tensors`、`merged_probe_changed_elements`、`resolved_training_config` preset；GPU 用 `replica_gpus`/`restored_checkpoint`；Harness 按 **basename+内容哈希** 相等（clone_task 复制），artifacts 按 manifest 相等 | `train_task_meta_sft.py:366-385` report 字段；`train_four_gpu_phase.py:83-84` `replica_gpus`；`storage.clone_task:105-116` 复制同名 Harness | `test_sft_accepts_production_metrics_and_cloned_harness`、`test_sft_production_metric_tampering_fails`（9 参数）、`test_harness_content_change_fails_but_clone_path_allowed`；legacy `LEGACY_METRICS` 读旧字段→生产 SFT 被判 incomplete（`test_legacy_metric_fields_reject_production_sft`） |
| 4 | LocalTaskClient 用 `response["message"]` | `_short_generation` 改读 `response["message"]["content"]` | `task_client.py:88` 返回 `{'message':...,'usage':...,'binding':...}` | `test_short_generation_requires_message_contract`（choices-only 客户端→incomplete）；legacy `LEGACY_CLIENT` 用 `choices`→生产 message 响应 KeyError（`test_legacy_client_contract_rejects_production_response`） |
| 5 | 非法 child/problems 在任何 ensure/start 前阻断 | 先算 `problems`，非空即 incomplete，**不**调用 ensure/verify/短生成 | 设计 §6.3 副作用前阻断 | `test_illegal_child_blocks_before_any_service_side_effect`、`test_child_outside_root_blocks_before_services`（断言 ensure/start/client 零调用）；`MUT_PRE_SERVICE` 把 ensure 移到 problems 前→变异调用 ensure（`test_mutation_pre_service_blocking`） |
| 6 | parent 证据封存，SFT 重验后才筛正例 | rollout receipt 增 `evidence_hashes`（trajectories/performance/cost/coverage/effective_config/8 个生产实现文件/逐任务回执哈希）与 `receipt_paths`；`_bindings` 增 `production_implementation_sha256`；SFT 先 `_revalidate_parent_for_sft`（重算哈希+重验完整五条+调用证据）再 `select_positive_rows` | 设计 §6 阶段间防篡改 | `test_rollout_receipt_seals_production_and_artifacts`、`test_tampered_parent_evidence_blocks_sft`（改轨迹→SFT incomplete "evidence changed"） |
| 7 | 缓存失败不得 already_completed 成功退出 | `_cached_result` 重验 binding；仅 SUCCESS_STATUSES 返回 already_completed，失败/零正例保留原状态 + `cached_failure`；CLI ok 集合排除 incomplete/blocked/requires_audit | 设计 §4 失败语义 | `test_cached_failure_is_not_already_completed`（CLI exit 1）、`test_zero_positive_cache_stays_blocked`（exit 1）、`test_successful_cache_reverifies_binding`（篡改 selection→requires_audit）；legacy `LEGACY_CACHE` 把 incomplete 报成 already_completed（`test_legacy_cache_reports_failure_as_success`） |
| 8 | 所有阶段共同互斥锁，锁内复查状态/registry | 单一 `locks/stage.lock`（O_EXCL）覆盖 rollout/sft/recovery；取锁后重新 `_stage_state`+binding 检查 | 设计 §4 禁并发/跨阶段 cleanup | `test_common_lock_blocks_concurrent_stages`、`test_state_rechecked_inside_lock` |
| 9 | SIGTERM/SIGINT 进入 finally owned cleanup | `_signal_guard` 安装处理器抛 `_Interrupted`→except→finally cleanup，退出恢复原 handler | 设计 §10 信号清理 | `test_signal_triggers_owned_cleanup`（SIGTERM/SIGINT 两参数：ensure 内自发信号→incomplete + stop 调用 + cleanup PASS） |
| 10 | registry 进程归属须确属本 smoke root | `_registry_owned_by_root` 除路径包含外，逐记录核验 serving config 在 `root/services/four_gpu` 内、其 `output_root==root`、launch command 引用该 config、PID identity 未变；拒绝替换的外部 registry | `gpu_phases.start_services:113` 记录 `config`/`command`/`identity`；`stop_services` PID/脚本检查 | `test_foreign_registry_replaced_inside_root_is_refused`（外部 output_root→拒停）、`test_owned_registry_with_matching_processes_is_accepted`；`MUT_CLEANUP_OWNERSHIP` 关闭归属检查→变异停止外部 registry（`test_mutation_cleanup_ownership`） |

### 8.2 RED/GREEN 与测试

- **RED 证据**：5 个 `test_legacy_*` 把对应修复回退为旧行为后，新合同断言失败（SimpleNamespace 无 model_dump、生产行被 trajectory_path 拒、生产 SFT 被旧字段判 incomplete、message 响应被 choices 解析 KeyError、incomplete 缓存被报成 already_completed）；6 个 `test_mutation_*` 证明零正例/manifest 绑定/归属/world_size/SFT 前置门/副作用前阻断守卫载荷性。
- **GREEN**：`RSIFlow/tests/test_training_side_smoke.py` **59 passed in 5.01s**。
- 相关 RSIFlow（training_side_smoke / task_adapter / service_startup / model_scoring_smoke / 600_744_protocol / release_integration）：**155 passed, 2 skipped in 38.75s**。
- reproduction 全套：**66 passed in 49.55s**。
- 完整 RSIFlow 全量由主 agent 复跑（其当前基线 420/2）；本轮未冒充全量结果。

### 8.3 v5 新 plan（真实资产，离线，无 GPU/模型）

新输出根 `reproduction/logs/training-side-smoke-plan-v5-r2`（写入前 `test ! -e`），parent 取 **v5** B1：

```json
{"command":"plan","ok":true,"status":"planned","output_root":".../training-side-smoke-plan-v5-r2"}
```

- `model_calls=0`、`selected_tasks=5`；parent B1 `manifest_hash=f969d3957160ef144792b7b5d2d1d4f2f3dd0fe82c38416d3dac6b6421727dd7`，mini `manifest_hash=f800fc64a3b296a552890257c1f7d1e4cce72ab45a7e9ea16adfc613a3adf631`。
- 五条与 v4 plan 相同（训练 B1 在 v4/v5 间逐字节一致）：`envscaler:env_154_rl:env_154_rl-task_47`、`deepcoder_taco:deepcoder-taco-424`、`deepcoder_taco:deepcoder-taco-2865`、`hotpotqa:5abaf8be5542992ccd8e7e85`、`2wiki:f96fae0a0bae11ebab90acde48001122`。
- 独立复核：mini 五条记录按原 offset/length 读回与 **v5 B1 bytes/content_hash 5/5 一致**；`bindings.production_implementation_sha256` 封存 **8** 个生产实现文件；effective quotas `{tool_use:1,code:2,searchqa:2}`、`num_train_epochs=1.0`、`max_steps=-1`。

### 8.4 本轮自审与边界

- 只改 runner/test/`REPRODUCTION.md`/追加本报告；**未改生产代码**、正式配置、frozen assets、JSONL 账本。
- 所有测试仍为临时 fixture + mock，**无真实 GPU/SCO/API/模型/五轮/SFT**；legacy/mutation 变异只作用于 tmp 副本（`module.ROOT`/`__file__` 指回真实路径以对齐 binding 哈希），真实 runner 与生产文件从未被修改。
- 真实 S1/S2/S3 **仍未执行**，需单独授权；本轮关闭的是生产合同一致性与执行安全守卫，不等于训练 smoke 通过或模型提升。
- 秘密扫描 0 命中、空白 0 问题；`git diff --check` 与 RSIFlow 全量留待属主/主 agent。
- **仍不能关闭**：正式五轮/600-task B1、候选接受/Meta、大规模 SFT 质量、五类 final benchmark/BFCL 152/694/ACE、正式 preflight 与长跑审批（设计 §12）。

## 9. Fix round 2（回应 scoped re-review 的 5 项 Important）

re-review 判定 #3/#6/#9/#10 NOT ADDRESSED 并新增纯 tool_calls Important。本轮逐项先 RED 再修，**信号与 registry 归属改用真实子进程 + `/proc` 证据**，不再仅靠 mock。

### 9.1 逐项关闭证据

| # | Important | 修复 | 关闭测试（含真实进程证据） |
| --- | --- | --- | --- |
| 1 | SFT 必须要求 `resolved_training_config` 及全部 preset 证据；`sampled_indices` 非空且四 rank 合并精确覆盖本次全部 eligible positive（一 epoch 每样本一次，重复/缺失/越界拒绝） | `_validate_sft_evidence` 改为：resolved 缺失/空 → problem；逐 preset 键**必须存在且相等**（缺失即 problem）；`lora_target_modules` 必须等于七模块；逐 rank `sampled_indices` 非空，合并后无重复、恰为 `set(range(n_positives))`，且 `positive_trajectories==n_positives` | `test_sft_requires_resolved_config_and_full_preset`（删 config / 删 max_steps 键）、`test_sft_sample_coverage_must_be_exact`（empty/duplicate/missing/out_of_range 四参数）；夹具按真实 `select_positive_rows` 计数并分区 sampled_indices |
| 2 | parent 封存加入 a0.json、实际 base_config、smoke launcher/effective config 及 SFT 重读的全部输入；SFT 前重算，改 checkpoint/Harness/base config 必须阻断 | 新增 `_training_input_hashes`（a0_state/a0_checkpoint/a0_harness_path/a0_harness_sha256/base_config/smoke_launcher/effective_config/base_checkpoint_manifest）与 `_base_config_path`（按 launcher `runtime_source`/`base_config` 解析实际 base）；纳入 `evidence_hashes.training_inputs`，`_verify_rollout_evidence` SFT 前重算 | `test_tampered_a0_state_blocks_sft`、`test_tampered_base_config_blocks_sft`、`test_tampered_harness_blocks_sft`（均 → incomplete "evidence changed"） |
| 3 | SIGTERM/SIGINT 在 adapter.sft 期间须清理并等待本 smoke 拥有的 phase/DDP 后代进程组；只用可核验 descendant/PGID/cmdline/output-root 归属，不全局 kill | 新增 `_training_owned_by_root`（training_pid 存活、PGID==pid、`/proc` cmdline 含 record 的 `--request-dir` 且该 dir 在 root 内、含 `train_task_meta_sft.py`）与 `_terminate_owned_training`（仅对核验通过的 PGID `killpg(SIGTERM)`→等待→`SIGKILL`）；execute_sft `finally` 调用 | `test_sft_signal_terminates_owned_training_pgid`（SIGTERM/SIGINT：真实 `start_new_session` 子进程，cmdline 含 trainer 脚本与 root 内 request-dir → 被终止并 wait，poll 非 None）、`test_sft_signal_refuses_foreign_training_pgid`（request-dir 在外部 root → 拒杀，进程仍存活） |
| 4 | 每次 ensure_services 前先做 registry 归属检查；除自述外对照 `/proc` 存活 PID 实际 cmdline/config/start identity；外部替换 registry 须在 production ensure 可能 stop 之前拒绝；finally 同样严格 | `_registry_owned_by_root` 增 `/proc/<pid>/cmdline` 核验（须引用本 root serving config 与记录的 launcher 脚本）；新增 `_assert_registry_owned`，在 rollout/sft/recovery **每次 ensure 前**调用；`cleanup_owned_services` 沿用同一严格检查 | `test_foreign_registry_rejected_before_ensure`（外部 output_root + 真实存活 PID → ensure 零调用、incomplete）、`test_live_cmdline_mismatch_rejected_before_ensure`（output_root 相符但存活 cmdline 不引用 config → 拒停、进程存活） |
| 5 | 合法纯 tool_calls 消息允许 content 为空；要求非空 tool_calls 结构；content 与 tool_calls 都空才阻断 | 新增 `_message_has_evidence`（content 非空 **或** 结构化 tool_calls 含 function.name+arguments）；`_validate_transport_calls` 与 `_short_generation` 改用之 | `test_pure_tool_calls_message_is_accepted`（用生产 `qwen_message()` 解码纯工具标记 → content=="" 且 tool_calls 非空 → rollout completed）、`test_both_empty_message_is_rejected`（双空/空 name → incomplete） |

### 9.2 RED/GREEN 与测试

- 每项先以生产合同写测试观察失败再修：preset 缺失/样本覆盖、a0/base/Harness 篡改、SFT 阶段真实子进程信号、ensure 前外部 registry/`/proc` cmdline、纯 tool_calls。
- **GREEN**：focused **74 passed in 7.81s**（含 fix round 2 新增 15 项：preset 2 + 覆盖 4 + 篡改 3 + 信号 3 + registry 2 + tool_calls 2，扣除参数化计数）。
- 相关 RSIFlow：**170 passed, 2 skipped in 42.43s**；reproduction 全套：**66 passed in 48.41s**。
- 完整 RSIFlow 全量由主 agent 复跑（其当前基线 431/2）；本轮未冒充全量结果。

### 9.3 v5 新 plan（r3，真实资产，离线）

新输出根 `reproduction/logs/training-side-smoke-plan-v5-r3`（`test ! -e`）：`status=planned`、`model_calls=0`、`selected_tasks=5`；parent B1 hash `f969d3957160ef14…`、mini hash `e6350ca1de0ed730…`；plan receipt SHA256 `dd58f95fe79705df71ca43c6287437f89dc098a9ed65fed301c72b3f81bda230`；`bindings` 封存 8 个生产实现文件。`training_inputs`（a0/base_config/launcher/harness）在 rollout 阶段封存并由 SFT 重验（plan 阶段尚无 a0）。

### 9.4 本轮自审与边界

- 只改 runner/test/`REPRODUCTION.md`/追加本报告；**未改生产代码**、正式配置、frozen assets、JSONL 账本。
- 信号/registry 测试 spawn 真实 `start_new_session` 子进程并在 finally 回收；**无真实 GPU/SCO/API/模型/五轮/SFT**；被终止的仅为测试自有的 sleeper 进程组，外部进程拒杀并验证仍存活。
- 真实 S1/S2/S3 **仍未执行**，需单独授权；本轮关闭的是训练中断安全、真实 PID 归属、训练配置/样本覆盖证据与纯 tool_calls 兼容，不等于训练 smoke 通过或模型提升。
- 秘密扫描 0、空白 0；`git diff --check` 与全量留待属主/主 agent。
- **仍不能关闭**：设计 §12 全部正式结论（五轮/600、候选接受/Meta、大规模 SFT 质量、五类 final benchmark/BFCL 152/694/ACE、正式 preflight 与长跑审批）。

## 10. Fix round 3（回应 fix round 2 re-review 的 3 项 + 1 新 Important）

re-review 判定样本覆盖、PGID 清理、ensure 前归属仍 NOT ADDRESSED，并新增"S2 未先重启 base"breakage。本轮逐项先 RED 再修，**保持此前所有修复**。

### 10.1 逐项关闭证据

| # | Important | 修复（生产合同依据） | 关闭测试 |
| --- | --- | --- | --- |
| 1 | 样本覆盖须按生产 `sft_positive.jsonl`/长度过滤/去重/DistributedSampler padding 语义，不能要求原始 positives 数与四 rank 无重复完全一致 | 读生产 trainer：`read_positive_rows`→`deduplicate_rows`（`sft_selection.json` 的 `input_rows/unique_rows`）→`rows`；`positive_trajectories=len(rows)`；Trainer 默认 DistributedSampler `drop_last=False` 补齐到 world 倍数；`train_task_meta_sft.py:344` 要求 `set(sampled_indices)==set(range(len(rows)))`，**允许 padding 重复**。runner 改为：以 `positive_trajectories`/`sft_selection.unique_rows` 为最终数据集大小，交叉核对 `sft_positive.jsonl` 行数==`input_rows`，合并 sampled 的**并集**恰覆盖 `range(n_final)`、索引在界内、允许重复；不再要求等于筛选前 positives 数或全局唯一 | `test_sft_padding_duplicates_are_legal`（5→pad 8，重复合法）、`test_sft_dedup_reduces_final_dataset_legally`（input>unique）、`test_sft_single_sample_with_padding_is_legal`（n=1 四 rank 各 [0]）、`test_sft_real_coverage_violations_fail`（missing/out_of_range/empty 真正漏样/越界/空） |
| 2 | 信号清理须等待整个 PGID 无存活成员（不只看组长）；覆盖组长先退 rank 仍存活；覆盖 phase 启动 DDP 后 `training_pid` receipt 未写的中断窗口；只用可核验 descendant/PGID/cmdline/root 归属 | `_group_alive` 改为按 `/proc` 非僵尸成员判定（组长僵尸不再误判存活）；`_training_owned_by_root` 组长退出时经 `_group_members` 找存活 rank 成员 cmdline 证归属；新增 `_discover_owned_training_groups`（扫描 session-leader 且 cmdline 含 trainer 脚本 + root 内 `--request-dir/--runs-dir`）覆盖 receipt gap；`_kill_owned_group` `killpg(SIGTERM)`→等待整组→`SIGKILL` | `test_sft_signal_kills_whole_group_after_leader_exits`（真实 leader fork rank 后退出，rank 仍存活 → 整组被终止，成员均 None）、`test_sft_signal_discovers_group_without_training_receipt`（无 training_pid，discovery 找到并终止）、原有 `test_sft_signal_terminates_owned_training_pgid`/`refuses_foreign` 保留 |
| 3 | 调用 `adapter.sft` 前先 registry 归属检查；S1 已停 base 后 S2 必须 ensure+verify base 四服务再让生产 phase 停止并训练；替换 registry 须在生产 `stop_services` 前拒绝 | execute_sft 在 `adapter.sft` 前：`_assert_registry_owned`→`ensure_services(base)`→`verify_services(base)`（GPU 0–3），再交生产 phase（其自身 `stop_services`）；非法 child 仍阻断 **child** 服务（base 重启为 S2 必需前置） | `test_sft_checks_registry_before_adapter_sft`（外部 registry → adapter.sft 零调用、无 ensure）、`test_s2_ensures_and_verifies_base_before_training`（SERVICE_CHECKPOINTS 前两位为 base ensure/verify，sft 后才 child ensure/verify） |
| 4 | 保持此前所有修复 | fix round 1/2 全部测试保留并通过 | 全套 80 passed |

### 10.2 RED/GREEN 与测试

- 先以生产采样合同/真实进程树写测试观察失败：旧"无重复完全一致"覆盖断言拒绝合法 padding/dedup；旧 `_group_alive` 把僵尸当存活致清理超时误判；旧 execute_sft 未先 ensure base。
- **GREEN**：focused **80 passed in 10.55s**；相关 RSIFlow **176 passed, 2 skipped in 46.77s**；reproduction 全套 **66 passed in 51.77s**。
- 完整 RSIFlow 全量由主 agent 复跑（其当前基线 446/2）；本轮未冒充全量结果。

### 10.3 v5 新 plan（r4，真实资产，离线）

新输出根 `reproduction/logs/training-side-smoke-plan-v5-r4`（`test ! -e`）：`status=planned`、`model_calls=0`、`selected_tasks=5`；plan receipt SHA256 `4dcc493f5f27f2e3026d46ddd0583b89efc2a19f903709ec5bbe79bb6c489e4f`。

### 10.4 本轮自审与边界

- 只改 runner/test/`REPRODUCTION.md`/追加本报告；**未改生产代码**、正式配置、frozen assets、JSONL 账本。
- 信号测试 spawn 真实进程树（leader+rank、独立 session leader），finally 回收；`pgrep` 确认**无残留 sleeper**；外部进程拒杀。无真实 GPU/SCO/API/模型/五轮/SFT。
- 真实 S1/S2/S3 **仍未执行**，需单独授权；本轮关闭的是生产采样覆盖语义、整组中断清理与 receipt gap、S2 base 重启与 ensure 前归属，不等于训练 smoke 通过或模型提升。
- 秘密扫描 0、空白 0；`git diff --check` 与全量留待属主/主 agent。
- **仍不能关闭**：设计 §12 全部正式结论。

## 11. Fix round 4（回应 fix round 3 re-review 的两项 Important）

re-review 判定采样证据缺失会跳过对账、discovery 只接受存活组长两项 NOT ADDRESSED。本轮只修这两项，**保持其他全部修复**。

### 11.1 逐项关闭证据

| # | Important | 修复 | 关闭测试 |
| --- | --- | --- | --- |
| 1 | selection 为空/缺失、`sft_positive.jsonl` 缺失、input/unique/positive_trajectories 不一致都必须 problem，禁止跳过对账 | `_validate_sft_evidence` 改为：`sft_selection.json` 缺失/空 → problem；`sft_positive.jsonl` 缺失 → problem；`input_rows` 必须等于实际行数、`unique_rows` 必须等于 `positive_trajectories`、两者必须为 int 且 `input_rows>=unique_rows`；不再以 `if selection:`/`if positive_path.is_file():` 跳过 | `test_sft_missing_selection_file_fails`、`test_sft_missing_positive_file_fails`、`test_sft_empty_selection_fails`、`test_sft_shrunk_self_report_fails`（缩小 positive_trajectories+采样但文件不变 → unique_rows 不符）、`test_sft_inconsistent_input_rows_fails`（自述 input_rows 与文件不符） |
| 2 | 无 training_pid receipt 且 leader 已退、rank 仍存活时，discovery 须从存活 rank 的实际 cmdline + root request-dir 发现 PGID 并核验整组归属后清理；覆盖组合情形与外部 rank 拒杀；不要求组长存活、不全局 kill | `_discover_owned_training_groups` 改为扫描**所有存活进程**（不限组长），凡 cmdline 含 trainer 脚本 + root 内 request/run-dir 即按其 `pgid` 归组；新增 `_discovered_group_owned` 在 kill 前以存活匹配成员复核整组归属；`_terminate_owned_training` 对 discovered 组先核验再清理 | `test_discovery_finds_group_when_leader_exited_no_receipt`（真实 leader fork rank 后退出、无 receipt → discovery 找到 rank 的 PGID 并整组终止）、`test_discovery_refuses_foreign_rank_group`（外部 request-dir 的 rank 组不被发现/拒杀、仍存活）、`test_discovery_combined_receipt_and_leader_exited`（receipt + leader 退出组合） |

### 11.2 RED/GREEN 与测试

- 先写负例观察失败：旧 `if selection:`/`if positive_path.is_file():` 跳过路径使删文件/缩小自述通过；旧 discovery 只接受 `pgid==pid` 的存活组长，漏掉 leader 已退的 rank 组。
- **GREEN**：focused **88 passed in 15.58s**；相关 RSIFlow **184 passed, 2 skipped in 53.48s**；reproduction 全套 **66 passed in 53.05s**。
- 完整 RSIFlow 全量由主 agent 复跑（其当前基线 452/2）；本轮未冒充全量结果。

### 11.3 v5 新 plan（r5，真实资产，离线）

新输出根 `reproduction/logs/training-side-smoke-plan-v5-r5`（`test ! -e`）：`status=planned`、`model_calls=0`、`selected_tasks=5`；plan receipt SHA256 `8e84e0571c86509ee3bac01e37b1369157412a11f99bb5161c20533ffdc6595e`。

### 11.4 本轮自审与边界

- 只改 runner/test/`REPRODUCTION.md`/追加本报告；**未改生产代码**、正式配置、frozen assets、JSONL 账本；fix round 1–3 全部测试保留通过。
- discovery 测试 spawn 真实 leader+rank 进程树，finally 回收；`pgrep` 确认**无残留 sleeper**；外部 rank 组拒杀并验证仍存活。无真实 GPU/SCO/API/模型/五轮/SFT。
- 真实 S1/S2/S3 **仍未执行**，需单独授权；本轮关闭的是采样证据强制对账与 leader 退出后的整组发现/清理，不等于训练 smoke 通过或模型提升。
- 秘密扫描 0、空白 0；`git diff --check` 与全量留待属主/主 agent。
- **仍不能关闭**：设计 §12 全部正式结论。
