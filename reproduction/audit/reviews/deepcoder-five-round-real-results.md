# DeepCoder function 16 单域五轮真实结果

> 日期：2026-11-19（UTC）
> 状态：**A0+B1–B5 工程协议完成，launcher 正常退出，external cleanup 完成。**
> 接受证据：`reproduction/audit/evidence/five-round-final-summary-20261119.json`（主 checkout 审计证据，只读引用）。
> 范围边界：这是固定同一批 16 条 DeepCoder/TACO function 的五轮工程实验，不是正式五域五轮、独立 benchmark 或作者原始实验复现。

## 1. 完成判定与协议范围

- `run_complete=true`、`completed_rounds=5`、`protocol_complete=true`、`launcher_exited=true`；`external_cleanup.status=completed` 且 `ok=true`，无 run-complete blocker。
- A0 与 B1–B5 的 required milestones、每轮 `attempt_1`、selection、active/skills/context/snapshot、B5 后 finish 均通过原始回执重建。state 为 `complete/completed`，workflow 为 `COMPLETE`，`finish_allowed=true`。
- 五轮使用**同一组 16 个唯一 task、相同顺序**，共 80 个 rollout positions；不是 80 个唯一任务。每轮父子分母均为 16，task IDs/source hashes/manifest bindings 配对一致。
- 每轮只有一个实验 attempt 和一个候选。ARTIFACTS 使用 `direct_submission_same_tasks_v1`，表示 **Meta-assisted direct-submission output repair**；候选不重新生成轨迹，新增 Qwen calls 为 0。

## 2. 逐轮真实结果

“Calls” 是父子两侧实际 Qwen trajectory calls；“full verifier” 仅计有有效 submission、实际进入完整官方验证的任务数，不能把分母 16 误写成 16 个完整测试。Transitions 列为 new success / new regression。

| 轮次 | 组件 | Calls 父→候选 | full verifier 父→候选 | 成功数 父→候选 | Transitions | 决定与激活 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| B1 | ARTIFACTS | 87→0 | 7→7 | 2/16→3/16 | +1 / +0 | `accept`，已激活 |
| B2 | MODEL | 40→118 | 6→7 | 4/16→3/16 | +2 / +3 | `retain_parent`，未激活 |
| B3 | ARTIFACTS | 61→0 | 8→8 | 2/16→3/16 | +1 / +0 | `accept_candidate`，已激活 |
| B4 | ARTIFACTS | 59→0 | 8→8 | 2/16→3/16 | +1 / +0 | `accept_candidate`，已激活 |
| B5 | ARTIFACTS | 63→0 | 8→8 | 4/16→5/16 | +1 / +0 | `accept_candidate`，已激活 |

各侧 full verifier 数量仅为 6–8；其余任务不伪称收到完整测试。B1/B3/B4/B5 每轮只编辑一个当前 artifact target，并复用其余 15 个 baseline results；B3/B4/B5 分别继承此前的 1/2/3 个 artifact records。四次 ARTIFACTS 接受均有严格 +0.0625 固定批净增益，B2 为 -0.0625 并保留父代。

## 3. B2 MODEL positive-only SFT

B2 真实执行了一次 positive-only LoRA SFT，训练证据由 `round_2/attempts/attempt_1/build/model_update/` 下的 request、selection、metrics、rank records、GPU phase 和 candidate state 共同绑定：

- 输入 20 条正例、20 条 unique，最终 sampled training examples=20；四个 rank 各 5 条，rank/local_rank 为 0/1/2/3。
- one epoch、`max_steps=-1`、learning rate `2e-6`、LoRA rank 8、batch size 1、gradient accumulation 8；world size=4。
- 每 rank optimizer steps=3，全局 metrics optimizer steps=3，`num_train_epochs_completed=1.0`；training loss `0.06101469943920771`。
- merged candidate 的 checkpoint/weights 与 base 不同，但配对结果为 4/16→3/16，因此**候选未激活，B2 snapshot 选择父代**。这次 SFT 只证明训练/merge/reload 工程链路真实发生，不证明模型质量提升。

MODEL SFT 仅发生在 B2；`model_sft.status=completed`，无 pending/failed MODEL round。

## 4. Active、model、artifacts 与 snapshot 链

| 选择点 | selected generation | selected model | 累计 artifact records | snapshot 选择 |
| --- | ---: | --- | ---: | --- |
| A0 | 0 | base Qwen3-4B | 0 | A0 active |
| B1 | 1 | base Qwen3-4B | 1 | candidate |
| B2 | 1 | base Qwen3-4B | 1 | parent；拒绝 merged MODEL candidate |
| B3 | 2 | base Qwen3-4B | 2 | candidate |
| B4 | 3 | base Qwen3-4B | 3 | candidate |
| B5 | 4 | base Qwen3-4B | 4 | candidate |

- 最终 active checkpoint 仍是 base Qwen3-4B 及其原三权重分片 manifest，**不是 B2 merged child**；最终 active 为 base model + 四个累计 artifacts。B2 candidate 使用另一个 merged checkpoint，但未进入 selected/active 链。
- 每轮 `snapshot_present=true`、`products_complete=true`，parent/candidate/selected state identity、checkpoint/weights、Harness bytes、artifact manifests、snapshot seals 和 previous-selected parent source 均通过汇总器重算。
- 最终 `active_task.json` SHA256 为 `8f75bce87740567949ba50cba15cce2179e477f3c79fdacedaf1512c679abeb1`。
- skills 与 context 均存在并沿五轮保存；最终 skills SHA256 `c07459e54602e8cc4e8dbca57bea681b21bdb9137d717275731270e703da2c19`，context SHA256 `5e7e763171036410f60a7e0dde5ab113bc56c0ecf3bd58f87c1861bf343ece51`，meta state SHA256 `eba1b18807f51b61c2e721fd9a8603556780c49075d8dac06d349f8d37a5d704`。

## 5. Attempts、失败与恢复历史

- 实验层每轮严格为 `attempt_1`；没有 rejected-candidate attempt_2。最终 22 个 durable jobs 均有终态，missing/invalid/required_failed 均为空。
- supervision 真实 attempts 为 `0001`、`0003`、`0004`、`0005`、`0006`、`0007`、`0008`；`0002` 是已证明在任何 identity/process/product 前发生的 preflight rejection，不计作真实执行 attempt。
- `0001/0003/0005/0006/0007` 保留受控停止 exit 143；`0004` 保留 missing-key exit 1；最终 `0008` exit 0。早期持续 Astra 502、provider/transport 异常、preflight/cleanup 问题均作为历史保留，没有改写成一次顺利运行。
- 后续 supervision 在同一 run/config 下以 resume seals 和下一 milestone 恢复，不重复已完成的 A0/父代 rollout、候选或 SFT。所有真实 attempts 均已 accounted/reconciled；最终 identity binding、reconcile complete、exit zero 均为 true。

## 6. Cleanup 与 occupier 交接

- 最终 external cleanup receipt SHA256 为 `2edb6da598746c0c2905af7bb901565669656b597c6d347ad41ce8f09c79c621`，状态 completed/ok、blockers 为空。
- owned registry 为 `services_stopped`，registry SHA256 `226253f371d29bf77287396105612f9671ecc93fb00bd61f97a34b2cedc82b95`；最终清理时四个 GPU 均空闲。
- **自动恢复当时为 `restore_pending`**：由于 NVML PID ownership 未解决，自动恢复被 fail-closed 拒绝；最终 cleanup 证据仍保持四卡空闲，不改写该历史。
- **后续直接启动已恢复（2026-11-19）**：用户明确授权后，主 agent 重新核实四卡空闲，在 `tmux` 会话 `rsiflow-idle-occupier` 中运行 `bash run.sh --mem-mb 2500 --log-interval 20`。四个 rank 均持续训练，脚本日志各约 2519 MB，`nvidia-smi` 各约 3085 MiB、46–48% 利用率。日志为 `/data/wuxuecheng/z-occupier-ready/runtime/dev4/tmux-occupier-20261119.log`；这是 cleanup 后的独立操作，不伪造自动恢复 receipt。

## 7. 离线测试证据

- 最终修复后的完整离线套件为 **1031 passed, 2 skipped in 723.43s**，exit 0；MODEL/selection 终审专项为 6+15 passed。
- 独立 scoped re-review 为 Ready=Yes、Critical=0、Important=0；真实最终汇总重算与接受汇总 SHA256 完全一致。完整命令、网站 11/11、静态/密钥形态和合并前 HTTP 证据见 `reproduction/audit/reviews/deepcoder-five-round-final-verification.md`。

## 8. 限制与归因

- 没有 held-out evaluation、独立 validation/benchmark、最终 694/744、ACE 50 或 8B 结果；没有正式五域 3000-task 实验，也没有作者原始实验复现。
- 四次 ARTIFACTS 接受只支持固定批上的 **Meta-assisted output repair**，零新增 Qwen calls；不能解释为 Task policy、Harness 或通用能力提升。B2 SFT 候选被保留父代，最终 model 未改变。
- ENOENT/EAGAIN 错误提及与零调用失败叠加，限制因果归因。B1/B3/B4/B5 的新成功均与零调用历史重叠；B2 的 2 个新成功和 3 个回归均与零调用重叠。错误提及不是重新评分依据，逐 task transitions 以原始 verifier booleans 为准。
- 固定 16 条重复队列的净变化不能外推到其他任务、领域或独立测试集。公开内容仅引用脱敏汇总，不包含 raw prompts/responses、私有轨迹、凭据、私钥或运行时 PID 清单。
