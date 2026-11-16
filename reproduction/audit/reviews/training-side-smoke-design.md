# 训练侧最小真实 smoke 设计（只读设计，未实施）

日期：2026-11-16
工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`

## 1. 目标与非目标

目标是在后续获得单独授权后，用一个此前不存在的 Git-ignored 输出根目录，验证：

1. 从冻结训练 B1 中确定性地选择至少一条 EnvScaler、一条 DeepCoder/TACO STDIO、一条 DeepCoder/TACO function、一条 HotpotQA、一条 2Wiki；每条完成真实 Qwen rollout、领域评分和持久回执。
2. 仅使用上述 parent rollout 中自然产生且经评分验证的成功轨迹，完成一次 positive-only、one-epoch、四 rank LoRA SFT，merge 为新的独立 checkpoint。
3. 四个逻辑 GPU 0–3 服务重新加载新 checkpoint，校验路径和 weights manifest，完成一次短生成；随后停止服务、释放显存，再从持久回执恢复服务而不重复训练，最后再次清理。

非目标：不进入正式 A0+B1–B5，不运行完整 600-task parent/candidate 配对，不接受候选，不写 Meta skills，不修改 frozen release 或原 checkpoint，不运行最终 694/744、ACE、外部 Meta/API 或模型质量评估。smoke 只产生工程可用性证据，不产生模型提升结论。

## 2. 方案选择与理由

### 推荐：五条任务 smoke-only mini release，原样复用生产 TaskAdapter

从真实 B1 manifest 选择固定五条任务，在新输出目录构造独立 mini B1；派生 smoke-only 配置的 source quotas 为 `1/2/1/1`，每 pass 总数为 5。随后调用现有 `TaskAdapter.rollout()` 和 `TaskAdapter.sft()`。

优点：保留真实 record bytes/offsets、manifest hash、parent provenance、领域执行器、正例筛选、固定 SFT preset、四卡停服务/训练/reload 路径；无需新增领域评分器或训练器。mini B1 对它自身是完整集合，但 outer receipt 必须明确它不是正式 600-task B1。

### 不推荐：直接逐条调用领域 adapter

虽然初始化较快，但会绕过 `TaskAdapter` 的完整 parent 检查、`scoped_rollout`/`annotate_row`、SFT 来源绑定和标准回执，使“能答一条题”被误当成“训练控制流可用”。

### 本任务不采用：完整 600 单轮或五轮缩小版

完整 600 能覆盖规模与配对，但不符合最小 smoke 预算；伪造“缩小五轮”又会改变 round/disjoint/acceptance 语义。工程 smoke 与正式实验必须分开。

## 3. 现有可复用生产接口

- `TaskAdapter._config()` 将 launcher quotas 归一为三 domain quotas，并应用固定 SFT preset：`RSIFlow/task_adapter.py:40-90`。`training_quotas()` 只要求正整数及总数自洽，并没有把每次调用硬编码为 600：`RSIFlow/evolution_protocol.py:16-26`。
- `TaskAdapter.bootstrap()` 固定 A0 checkpoint、seed Harness 和 checkpoint manifest：`RSIFlow/task_adapter.py:133-151`。
- `TaskAdapter.rollout()` 可确保四服务、构造 executor、执行 parent pass 并写 performance/cost/trajectory 路径：`RSIFlow/task_adapter.py:261-291`。
- `TaskAdapter._executor()` 使用 `RoundStore`、`adapter_factory`、`MultiDomainExecutor` 和 `LocalTaskClient`；`Scope.observed()` 要求当前 mini B1 每个成员恰好一次，并标注 parent provenance：`RSIFlow/task_adapter.py:191-259`。
- `MultiDomainExecutor.execute()` 复用持久 rollout cache、执行评分、写 train trajectories/coverage/cost，并调用 round observer：`RSIFlow/runtime/rsiflow/task_meta/pipeline_execution.py:269-405`。
- `scoped_rollout()` 和 `annotate_row()` 固定 manifest、Task/Harness/checkpoint、round、branch、verifier 与 trajectory 身份：`RSIFlow/runtime/rsiflow/task_meta/round_evolution.py:62-87`。
- `TaskAdapter.sft()` 在 GPU 训练前要求 parent 行数、唯一 task IDs、total rollouts、role、round、manifest 和 `parent_pre_update` 完整一致：`RSIFlow/task_adapter.py:407-456`。
- `ModelUpdater` 只选择完整正 reward 文本轨迹，检查长度、固定 base checkpoint、新 weights 和服务绑定：`RSIFlow/runtime/rsiflow/task_meta/updaters.py:298-407`。
- `train_four_gpu_phase.py` 负责停止服务、四个 GPU lease、四个 local DDP ranks、失败恢复 base、成功/失败后服务重启及 rank 证据检查：`RSIFlow/runtime/scripts/train_four_gpu_phase.py:30-97`。
- `train_task_meta_sft.py` 实现正例读取、one-epoch/preset 校验、assistant-only loss、LoRA 变化检查、merge、checkpoint 保存和 rank metrics：`RSIFlow/runtime/scripts/train_task_meta_sft.py:50-70,163-216,219-413`。
- `gpu_phases.py` 提供 `registry_path/start_services/verify_services/ensure_services/stop_services`；健康绑定包括 GPU、checkpoint 和 weights，停止后要求 GPU 0–3 各至少 40,000 MiB 空闲：`RSIFlow/runtime/rsiflow/task_meta/gpu_phases.py:15-18,32-80,83-151`。
- `prepare_600_744.repack()` 可按原 offsets 复制真实 record bytes 并生成新 record hashes，`data_protocol.native_index()` 可建立 smoke `tasks.sqlite` 与检索索引绑定：`RSIFlow/prepare_600_744.py:43-74`、`RSIFlow/data_protocol.py:324-364`。

不调用 `pipeline.run()`：它会建立正式 run、Meta backend、全局实验 protocol 和演化状态，超出本 smoke 边界（`RSIFlow/runtime/rsiflow/task_meta/pipeline.py:276-362`）。

## 4. 必须新增的单一 runner

后续实施只新增 `reproduction/scripts/training_side_smoke.py`，并新增/扩展现有 `reproduction/tests/test_training_side_smoke.py`。不改生产 `TaskAdapter`、pipeline、gpu phases、trainer、领域 adapter、正式配置或 frozen assets。

runner 提供五个明确子命令：

```text
plan -> execute-rollout -> execute-sft -> execute-recovery -> status
```

`plan` 是唯一创建输出根目录的命令，默认无 GPU/模型调用。后续命令要求前一阶段的不可变 receipt 与当前代码/config/hash 完全一致。每个 execute 命令持有同一输出根下的 stage lock，禁止并发。最终 receipt 已存在时只校验并返回 `already_completed`；存在 started 而无 final receipt 时标记 `requires_audit`，不得静默重训或删除重来。

建议目录：

```text
<new-output-root>/
  plan/selection.json
  plan/smoke_launcher_config.json
  plan/effective_config.json
  plan/base_checkpoint_manifest.json
  release/B1/{manifest.json,*.jsonl}
  release/tasks.sqlite
  release/search.sqlite*
  runs/training_side_smoke/a0.json
  runs/training_side_smoke/parent/
  runs/training_side_smoke/sft/
  services/four_gpu_services.json
  receipts/{rollout,sft,recovery,final}.json
```

`config.output_root` 必须是该新根目录；SFT output 必须位于其 `runs/` 下，以满足 trainer 的 contained-path 约束。新 merged checkpoint 只能位于该树内。所有源 checkpoint、v4/v5 release 和正式 `Rollout_logs`/`Meta_logs` 路径只读。

## 5. runner 内最小 helper

只新增编排和验收 helper，不复制生产算法：

1. `select_training_subset(parent_manifest) -> selection`
   按预先声明的 parent manifest 顺序，分别取第一条 EnvScaler、STDIO、function、HotpotQA、2Wiki；要求恰好五个唯一 task IDs，并记录原 B1 manifest hash、record file/hash/offset/length、group/content hash 和选择规则。禁止根据模型结果重选。
2. `materialize_mini_release(selection, root, retrieval_config) -> release_identity`
   用 `make_manifest`、`repack`、`freeze`、`native_index` 创建独立 B1；回读五条 record bytes 和 hashes，验证原 B1 与检索索引未变。
3. `derive_smoke_config(machine_config, root, release) -> effective_config`
   派生 `rounds=1`、`allocated_tasks=5`、`training_tasks_per_pass=5`、source quotas `1/2/1/1`、独立 data/output/run 名；保留真实 checkpoint、Harness、检索、seed、BF16、one-epoch LoRA 和 SFT preset。再由 `TaskAdapter._config()` 验证并冻结 effective config。
4. `run_and_validate_parent(config, state_path, output_dir) -> receipt`
   调用 `bootstrap` 和 `rollout(stage=parent, ensure_services=True)`；验证五个 IDs、五份评分、role/round/manifest/stage、每条至少一次真实本地模型调用，以及无 infrastructure error。
5. `run_and_validate_sft(config, state_path, parent_dir, output_dir) -> receipt`
   先用生产 positive selector 判断是否存在 eligible positive，再调用 `TaskAdapter.sft()`；验证四 ranks、one epoch、正例来源、base 不变、新 checkpoint 不同且 weights 改变、Harness/artifacts 不变、四服务绑定 child。
6. `recover_and_probe(config, child_state, sft_receipt) -> receipt`
   核对训练 receipts/checkpoint hashes 未变；用 `ensure_services` + `verify_services` 恢复 child 四服务，执行一次 `LocalTaskClient` 短生成；整个过程不得再次调用 SFT。
7. `cleanup_owned_services(config) -> cleanup_receipt`
   只操作本输出根的 registry，在 `finally` 中停止 owned process groups、检查显存和进程；保留所有 checkpoint、日志和回执，不删除证据。
8. `emit_stage_receipt(...)`
   记录 code commit、config/selection/manifest hashes、起止时间、退出状态、错误类别、资源清理和 artifact hashes；公开快照只含脱敏计数与 hash，不含私有轨迹。

## 6. rollout 与 SFT 验收判据

### 6.1 五条 rollout

每条必须满足：

- source/interface 覆盖完整：`envscaler=1`、`deepcoder_taco{stdio}=1`、`deepcoder_taco{function}=1`、`hotpotqa=1`、`2wiki=1`。
- task ID 属于冻结 mini manifest；record hash/offset/length 可读回相同 JSON bytes。
- `source_role=train_evolution`、`purpose=evolution_train`、`round_id=1`、`collection_stage=parent_pre_update`，manifest/harness/checkpoint binding 与 plan 一致。
- `model_call_count>=1`，存在持久 per-task receipt 和 trajectory；`verification.status=completed` 且 `success` 为布尔值。
- 不存在未解释的 infrastructure error、unknown score、缺失 scoring receipt 或重复 task ID。

模型答错但评分完成是有效 smoke 结果，不是基础设施失败，不能改成模型零分之外的特殊“通过”。五条 coverage 可以通过，但如果没有正例，SFT 仍不可执行。

### 6.2 零正例与重选边界

固定五条可能全部失败。此时：

- rollout coverage 可单独记为 `completed_with_model_failures`；
- SFT 标为 `blocked_no_positive_parent_trajectory`，总训练侧 smoke 不得记 PASS；
- 不得手写 success、从 candidate/held-out 借轨迹、降低 terminal reward 要求或生成合成标签；
- 不得查看结果后换一条题再声称同一固定选择。若需扩大样本，必须另建输出根、预先声明新的确定性选择规则并单独授权。

### 6.3 positive-only SFT、merge 与 reload

SFT PASS 必须同时满足：

- `sft_positive.jsonl` 每条来自本次 parent 五条轨迹，terminal reward 为正且评分/消息/长度完整；负例和 infrastructure failure 不进入训练。
- effective training 保持 `num_train_epochs=1`、`max_steps=-1`、LR `2e-6`、LoRA rank 8/alpha 16/dropout 0.05、七个 target modules、batch 1、gradient accumulation 8、max length 16384、BF16/gradient checkpointing、seed 42。不得为加速改成 `max_steps=1`。
- `world_size=4`，rank records 恰好 `{0,1,2,3}`，每个 eligible sample 在一轮内被访问，optimizer/loss 为有限值，LoRA tensors 和 monitored merged weight 确实改变。
- child checkpoint 位于 smoke 输出树，路径与 base 不同；base checkpoint manifest 前后完全一致，新 weights hash 集合不同。
- `gpu_phase_receipt.status=completed`，`restored_checkpoint=child`，四个 replica GPU 为 0/1/2/3；`verify_services(config, child)` 的路径和 weights 全部匹配。
- 用 child 做一次真实短生成，存在 completed local transport 和 usage；不据此声称质量提升。
- 不写正式 active Task，不调用 candidate acceptance，不生成正式 round complete marker。

若训练失败，trainer 应恢复 base 并记录 `training_failed_requires_audit`；runner 保留失败、不得发布 child，总结果失败。 happy-path smoke 不等于已实测所有训练失败恢复分支。

## 7. 离线严格 RED 测试

测试只使用临时 fixture 和 mocks，不启动 GPU/模型/API/SCO，不生成真实 smoke 证据。先在 runner/helper 尚不存在时观察新测试 RED，再逐段实现到 GREEN；不能用删除断言或合成真实回执制造 GREEN。

至少覆盖：

1. **确定性与覆盖**：五条选择顺序稳定，STDIO/function 不混淆；缺任一 source、重复 ID、空 record、错误 role/hash/offset 均 RED。
2. **mini release**：record bytes 回读一致，manifest/task SQLite 只含五条；输出目录或 symlink 已存在 fail closed，原 B1/检索索引 hashes 不变。
3. **有效配置**：domain quotas 为 `1/2/2`、总 parent rows=5；固定 SFT preset 未被 smoke 覆盖，output/run/data roots 均隔离。
4. **阶段门**：无 plan 不能 rollout，无完整 parent 不能 SFT，无成功 SFT 不能 child recovery；正式 config/run 路径不能被复用。
5. **模型答错与基础设施失败分离**：false success 但 completed scoring 可作为有效行；missing score、infrastructure error、wrong manifest、child/candidate rows 均阻断。
6. **零正例**：不调用 `TaskAdapter.sft`/trainer，不写 candidate checkpoint 或 PASS receipt。
7. **正例调用链**：mock 观察 bootstrap→services/rollout→positive filter→stop/train/reload→verify→cleanup 顺序；验证使用原 `TaskAdapter` 接口而非替代 trainer。
8. **SFT 证据防篡改**：world size 1、缺 rank、非 one epoch、base hash 改变、child=base、weights 未变、错误 restored checkpoint、Harness 改变均失败。
9. **清理**：rollout exception、SFT exception、SIGTERM/SIGINT、短生成失败、stop failure 都写 `incomplete/failed` 并保留原异常；registry 不存在时不得停止其他 output root 的服务。
10. **恢复不重复训练**：mock recovery 时 `TaskAdapter.sft` 零调用，训练 receipt hashes 不变，只恢复/验证 child 服务并清理。
11. **超时/磁盘边界**：超时、过大的输出、越界 checkpoint path、非有限 metrics 均 fail closed；不得把 timeout 记成模型失败。
12. **plan 无副作用**：将 start/stop/train/LocalTaskClient 全部 patch 为“调用即失败”，plan 仍只写计划与 hashes。

现有 `test_task_adapter.py:189-212,363-384`、`test_service_startup.py:7-80` 和 `test_model_scoring_smoke.py:334-380,466-535` 可复用其接口 mock、cleanup 和 wrong-answer/infra-error 测试模式，但它们没有替代真实四卡 SFT。

## 8. SCO 阶段划分

所有作业前台运行、顺序执行，仅使用逻辑 GPU 0–3，不并行第二条流水线，不使用 `sleep inf`。真实执行前单独授权；每阶段先检查输出根、stage lock、owned registry 和 GPU 状态。

| 阶段 | 动作 | 成功后可进入下一阶段 | 失败处理 |
| --- | --- | --- | --- |
| P0 本机 plan | 固定五条选择、mini release、effective config、base/source hashes；无 GPU/API | plan receipt 完整且资产检查无 blocker | 只保留计划失败，不创建正式 run |
| S1 rollout SCO | 启动四个 base 服务，五条真实 parent rollout/评分/回执，最后停服务 | 五条 coverage/scoring 完整，cleanup PASS | SFT 阻断；保留部分 cache 与错误，不自动换题 |
| S2 SFT SCO | 重新启动 base 四服务；调用生产 SFT，停 base→四 rank DDP→merge→启动 child→verify+短生成→停 child | SFT/reload evidence 和 cleanup 全部 PASS | 若是 trainer failure，确认恢复 base；不发布 child，不进入 recovery PASS |
| S3 recovery SCO | 从持久 child state 启动四服务，校验训练 hashes 未变、短生成、最终停止 | recovery 和最终 cleanup PASS | 标记恢复失败，不重训、不删除旧输出 |

S1/S2 分开会在 S2 多加载一次 base，这是有意的阶段隔离成本，可避免依赖跨 SCO 作业存活的服务进程。若后续合并 S2/S3，仍须实际 stop 后再 ensure/start，不能把健康检查本身当作恢复测试。

## 9. 建议超时与预算

以下是 smoke 专用上限，不修改正式实验 budget：

| 项目 | 建议值 | 判据 |
| --- | --- | --- |
| P0 | 10 分钟 | 超时即失败；无模型调用 |
| S1 服务启动 | 沿用 1800 秒 | 四个 binding 全部 ready；不能只看端口 |
| S1 rollout | 五任务合计最多 3600 秒；每任务一次 rollout，每次请求 180 秒，model call limit 建议 16，max output tokens 2048 | 五任务/五评分/五回执；watchdog 超时进入 finally cleanup |
| S1 内部/SCO hard cap | 6000 / 7200 秒 | hard kill 后下一阶段先清理 owned registry，不直接重启 |
| S2 parent/child 启动 | 各沿用 1800 秒 | base 与 child 各自验证四卡 weights/GPU |
| S2 DDP | `training_timeout_seconds=3600`，无自动 retry | 保留固定 one epoch；超时终止整个 training PGID |
| S2 内部/SCO hard cap | 7800 / 9000 秒 | 包括启动、训练、reload 和清理 |
| S3 内部/SCO hard cap | 2700 / 3600 秒 | 只恢复服务，禁止重新训练 |
| 请求/输出预算 | 五任务、每任务最多 16 次本地调用；无外部 API 请求 | 超出即停止，不扩成正式 rollout |
| 输出预算 | 建议总量 40 GiB；一个独立 merged checkpoint，trainer save limit 沿用生产 | 越界 fail closed；不得写入 base checkpoint |

真实执行若需要更宽松的调用上限才能完成多步 EnvScaler，应在 plan 中提前修改并重新冻结 hash，不能运行中静默提高。

## 10. 清理、中断与恢复判据

每阶段 `finally` 和最终验收均要求：

- registry 属于本 smoke output root，phase 最终为 `services_stopped`；所有登记 PID 的 start ticks/PGID 不再存活。
- 四个 DDP rank/trainer process group 已退出，无本 smoke owned 子进程残留。
- `stop_services()` 成功，GPU 0–3 每卡至少 40,000 MiB 空闲；若服务启动前没有 registry，则记录“未创建 owned 服务”，而不是停止其他任务。
- 8271–8274 不再有本 smoke ready 服务；不能据此杀其他 owner 的进程。
- base/source/frozen hashes 未变；所有新文件仍位于 output root，日志与回执保留。
- cleanup 自身失败时，总 smoke 必须失败，即使 rollout/SFT 已完成。

外部 SIGKILL/SCO 控制面终止可能绕过 Python `finally`。因此下一次启动必须先按 registry 的 PID identity 和 GPU 状态做 owned cleanup；无法确认 owner 时停止并请求人工核查，不能杀疑似进程或清空目录。

恢复证据只声明：同一 child checkpoint/weights 能在服务停止后重新加载，且 SFT artifacts 未变。任意 training crash 的自动续训不在本次范围内；已有 `gpu_phase_receipt` 或部分 `model_update` 时默认 `requires_audit`，不得删除后重跑。

## 11. 不跑完整 600 是否仍可信

**对“最小工程链路可信”可以，对“正式协议完成或规模可用”不可以。**

可信的原因是：选择在看模型结果前固定；五条记录来自真实 train B1，record/source hashes 和 offsets 可回读；完整 mini manifest 通过原 `TaskAdapter` parent 完整性、角色、stage、正例和 checkpoint 约束；训练使用原固定 trainer 与四卡相位，没有 mock/CPU 回退。相较于逐条 ad-hoc adapter 调用，它能关闭“真实训练控制流从未执行”的空白。

必须附带的限定：

- `Scope.observed()` 的 `same_round_full_B_parent_child_v2` 在本 smoke 中只表示“完整五条 mini B1”，绝不能单独引用该字符串宣称完成正式 600-task B1。
- outer receipt 必须包含 `smoke_only=true`、`selected_tasks=5`、`formal_round_completed=false`、`eligible_for_meta=false`、`eligible_for_candidate_acceptance=false`、原 B1 hash 和 mini manifest hash。
- smoke 只跑 parent，不跑 child 配对，不算一轮演化，不产生可比较的质量增益。
- 固定五条覆盖每类至少一条，但不能代表该类所有环境、难度、上下文长度或并发负载。

## 12. 仍不能关闭的结论

即使 S1–S3 全部 PASS，仍不能据此关闭：

1. 正式五轮 A0+B1–B5、五轮 3000 任务或每轮完整 600 parent/candidate 配对。
2. strict-positive-gain 候选接受、Meta 决策/技能学习、正式 snapshot/active-state 语义。
3. 完整 600 并发下的吞吐、长上下文、OOM、任务多样性、30 天 wall budget 和长期稳定性。
4. 大规模 one-epoch SFT 的样本覆盖、训练质量、灾难性遗忘或模型能力提升；五条 rollout 的分数不是 benchmark。
5. 所有训练失败/中断后的自动续训和真实 base-restore 负向分支；本设计只强制服务级恢复，训练失败恢复主要依赖既有代码与离线测试。
6. 五类 final benchmark 最小 smoke、BFCL native-ID/152 分母、最终 694/744、ACE 50 或其他独立评测门。
7. 当前正式 694 配置预检、正式长跑输出/恢复审批、Meta/API 长跑可靠性和完整资源隔离审批。
8. 五轮启动时的当前四卡健康：smoke 最后会主动停服务，正式作业仍需按当时 GPU/checkpoint 状态重新验证。

最终公开口径只能是“五条真实训练域 rollout + 一次真实四卡 positive-only SFT/merge/reload + 服务级恢复通过”，不能写成“五轮训练协议已通过”或“模型已改进”。
