# DeepCoder function 16 真实 canary 结果

- 日期：2026-11-17（UTC）
- 代码基线：`a74cf95c77ab197ab8f3e924984bb3d1dcb4a05b`
- 配置：`reproduction/configs/train_4b_deepcoder_canary_workspace.json`
- Run：`deepcoder_function_16_canary_workspace_v1`
- 真实结果审阅：Spec compliance **PASS**；Task quality **DONE_WITH_CONCERNS**。
- 范围：固定 16 条 DeepCoder/TACO **function**、code 单域、A0+B1 单轮、一个实验 attempt 和一个候选；不是五轮、正式 SFT、694/744 或作者原始实验复现。

## 1. 协议与真实完成状态

冻结 release 按父 B1 原顺序选取 16 条 function 记录，父代与候选的 16 个 task IDs、顺序和逐 ID source hashes 完全一致；没有换题。A0 只保存初始 Task/Meta 状态，不做 baseline validation。B1 完成父代 rollout、Meta evidence、单组件选择、一个候选、同批候选 rollout、paired comparison、显式选择、经验沉淀和最终 snapshot。

最终回执为：

- `state.json.status=complete`，`last_turn_status=completed`；
- workflow `current_stage=COMPLETE`、`finish_allowed=true`、observed_turns=88，A0/B1 milestones 无剩余；
- `finish_experiment status=complete remaining={}`；
- 9 个 durable jobs 都有最终结果；当前 supervision attempt 0002 exit 0、reconcile complete；
- owned service registry 最终为 `services_stopped`，四张 A800 清理后各 1 MiB、无计算进程。

## 2. Meta 实际选择与 HARNESS 候选

Astra 的 `decision.json` 和 `skill_use.json` 均记录 **HARNESS**。Meta 针对 Action final-answer handoff 写了 `module_localization_report.md` 与 `improvement_direction_brief.md`，materialize 完整 bundle，并只在同一实验 `attempt_1` 中构造一个候选。

同一 candidate 的可执行检查共两次，未超过三次上限：

1. turn 0066：`validation_failed` / `failed_build`，暴露 planning injection/wiring 问题；
2. 修复同一 bundle 后，turn 0071：`passed`，required files、syntax、exports、imports、submodule imports 和 build-agent smoke 全部通过。

生成的 candidate state 只改变 Harness：generation 0→1；Qwen3-4B checkpoint、三份 weights manifest 和 model identity 与父代相同。MODEL 未被选择，未执行 SFT、LoRA、merge、child reload 或训练后短生成。

## 3. 真实 Qwen 调用与官方 verifier 覆盖

独立审阅同时核对 declared calls、trajectory 内嵌 model/transport calls 和 rollout-cache call 文件：

| 指标 | Parent | Candidate |
|---|---:|---:|
| 固定 function tasks | 16 | 16 |
| 真实 Qwen calls | 51 | 93 |
| `verification.status=completed` | 16 | 16 |
| 有效 submission 进入 full verifier | 7 | 8 |
| 无有效答案、未进入 full verifier | 9 | 8 |
| Official success | 3 | 4 |

所有进入 full verifier 的任务均完成其 tests_completed=tests_total。父代 3 个成功分别通过 11/11、13/13、13/13；候选 4 个成功分别通过 11/11、10/10、8/8、6/6。其余 full-verifier submission 是真实官方失败。

“16 条 verification completed”不等于“16 条都执行了完整官方测试”：父代只有 7 条、候选只有 8 条有效 submission 进入 full verifier，其余不得伪称完整测试。

## 4. 配对分数、接受与激活

`compare_scores` 核验 before/after 均为 complete、total_rollouts=16、round_id=1，training manifest 和 probe identity 相同；16 shared IDs、same_id_set=true，无缺失、多余或重复，16 对 source hashes 全部一致。

| 配对结果 | 数值 |
|---|---:|
| Parent macro_success | **3/16 = 0.1875** |
| Candidate macro_success | **4/16 = 0.25** |
| Delta | **+0.0625（+6.25 个百分点）** |
| New success | **4** |
| New regression | **3** |
| Retained failure | **9** |

`selection.json` 明确记录 `accept_candidate`，满足当前 canary 的严格净正增益规则。turn 0083 为 `activated_as_requested`；candidate state 与最终 `active_task.json` 字节相同，SHA256 均为：

```text
deac8500960139a55e4b91f0546076080a5b12a73894c161ff2bcaf157dcd494
```

最终 active Task 指向 candidate Harness bundle，model/checkpoint 未变。

## 5. 关键质量限制：净增益不等于已证实的因果改进

`compare_task_differences` 和独立逐轨迹复核得到 **4 个新成功、3 个回归，且父代原 3 个成功全部回归**：

- 4 个新成功全部来自父代零模型调用失败任务；
- 3 个回归全部是候选零模型调用失败任务；
- 父代 9 个零调用过程失败包括 6 个 EAGAIN/BlockingIOError 和 3 个 ENOENT/FileNotFoundError；候选 8 个零调用过程失败包括 7 个 EAGAIN 和 1 个 ENOENT；
- 这些轨迹按当前协议记录 `infrastructure_error/failure=false`，完整保留在 16 条分母中，不事后改分或撤销接受；但现有证据无法分离 Harness 编辑与执行过程波动。

因此，接受只代表该固定 16 条训练批上的净正增益，**不能宣称 Harness 已证明普遍改进或模型质量提升**。目标机制有行为迹象，但 success gain 的来源未被隔离证实。

## 6. Skills、context 与 snapshot

- `append_skills` 追加一条 `skill.HARNESS.canary_action_handoff_1`，包含机制、4 new success、3 regressions、适用范围、未知项和证据引用；`prediction_assessment.met=null`，没有虚构预测已证实。
- `meta/context.json` 记录 accepted、3/16→4/16、4/3 transitions 和过程异常归因限制。
- `snapshots/round_1_attempt_1/` 保存 parent、candidate、selected Task states，以及 model_001、harness_001/002、meta_002 和 context reference；未复制模型 weights。
- snapshot 内 parent/candidate/selected 组合、ledger hash 和 context reference 经独立复算匹配。

## 7. Supervision 0001 与同 thread 恢复 0002

第三次 workspace run 的 supervision 0001 已完成 A0、父代和 HARNESS 前期产物，随后在 candidate_built 前遇到持续 Meta provider 502。精确事件口径为：

- **24 个 distinct failed turns**；
- **24 条 `method=error` 且含 502 的 events**；
- 另有 **24 条对应 `turn/completed` 且含同一 502 的 events**；
- 合计 48 个 event rows，但不得写成 48 个失败 turns。

attempt 0001 以 exit 143、complete reconcile 和 cleanup 结束，历史未删除。provider 健康探针恢复后，attempt 0002 在**同一 run、同一 config、同一 backend、同一 Astra thread** 上从 `candidate_built` 继续；没有第二次 bootstrap 或 run_parent，没有重放技能种子、换题或新建实验 attempt。0002 没有新增 502，最终 exit 0。

## 8. 关键公开哈希与证据入口

原始 prompts/responses、私有轨迹、凭据和大日志不公开。以下哈希用于绑定脱敏汇总与实际产物：

| 产物 | SHA256 |
|---|---|
| Parent trajectories | `b60c84d02c88c9b1ecd2d924274040b77949d1b2b1b0ca0109b0c8d884ae9579` |
| Candidate trajectories | `876088e4407260ef7c587190c1301785eba14619620b89c444a8d88804c68c44` |
| Parent performance | `26fb3307a29912fcde48323dc054f382671f21b26632716e81457c370dda479c` |
| Candidate performance | `b8846a242a3b25cd574f6ac3f34f63bab7b2ec2f1099c7e9bfd30375d0738dbd` |
| Selection | `6aacea59406d392e826285407d72a009fce4ed90381f2b4ae437909de1d50146` |
| Final workflow | `985fa670b6ee20698aee68b7f58c2d0e62496223cf642a292e9face9b6181c63` |
| Final state | `1876a0ecfd8c4e18236c70964afdf1f3951b192b28980126dac677367f6779cf` |
| Final snapshot receipt | `817bbed4b4facf9c9ac26d46da13414e63c8aefd028f3738a66b039a57612459` |
| Stopped service registry | `ca0426723bd6c54e0a49028b87656b895a9daf20d0d8e01f1e6f84bb45e684b5` |

完整执行与恢复记录见 `.superpowers/sdd/2026-11-17-deepcoder-canary/task-5-report.md`；独立原始产物审阅见同目录 `task-5-review.md`。当前完整离线套件计数为 **517 passed, 2 skipped**；skipped 不计为通过。

## 9. 明确未运行的范围

- 五轮 3000-task 演化：未运行；
- 正式 MODEL/SFT：未运行；
- 最终 694/744 与独立 validation/benchmark：未运行，ACEBench 为 skipped 而非 scored；
- 作者原始实验与原始 3000/744 队列复现：未运行；
- 8B：未运行。

本报告只发布上述单域单轮单候选工程 canary 的真实结果及其限制。
