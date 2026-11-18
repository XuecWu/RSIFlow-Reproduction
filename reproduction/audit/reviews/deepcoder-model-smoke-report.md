# DeepCoder MODEL 独立真实 smoke 报告

- 日期：2026-11-18（UTC）
- reviewed/execution HEAD：`88bdc288fee690344d021f787736ee385ac78a6d`
- 结论：**BLOCKED，真实 MODEL gate 未通过，execute 未运行。**
- 阻断点：`deepcoder_model_smoke.py plan`，在 MODEL output root 创建前 fail closed。

## 1. 实际执行与错误

在 dev4 显式设置 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`，使用 brief 指定的 source-run、machine config 和新 output root 执行一次 `plan`。没有改用 fake/host/Landlock，没有删除或替换 source，也没有尝试第二个 output root。

实际返回 exit 1：

```text
Source run contains a symlink: /data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/Rollout_logs/deepcoder_canary_16_v1/runs/deepcoder_function_16_canary_workspace_v1/meta
```

审计 operation `00001299`，stdout `reproduction/audit/evidence/00001299-stdout.log`，SHA256 `04be81fd6e5e9c6a76e4cbfa539cde16d224428b56c9f04451de7c3d7bfd2b07`。

## 2. 已确认根因

这是实现与真实生产 source-run 形状的冲突，不是 SSH、API、GPU 或 occupier 故障：

1. `reproduction/scripts/deepcoder_model_smoke.py:101-113` 的 `_source_hashes()` 拒绝 source tree 内任意 symlink。
2. `plan_model_smoke()` 在 `root.mkdir()` 之前调用该函数（当前脚本 `:396-426`），所以拒绝发生在 plan receipt、started lock、服务或训练副作用之前。
3. 真实 source 的 `meta` 是既有符号链接，目标为同一 preflight worktree 的 `Meta_logs/deepcoder_function_16_canary_workspace_v1/meta`；`meta_session` 当前不是 symlink。
4. 该形状不是异常注入：生产 `RSIFlow/launch_meta.py:130-159` 的 `prepare_meta_storage()` 明确为 `meta`/`meta_session` 建立集中存储链接，且离线测试覆盖这一生产行为。

因此，当前 reviewed 实现无法导入 brief 指定的真实 completed canary。Task 4 的 symlink 反例只证明 fail-closed，没有覆盖“生产正常创建的 `meta` 链接 + 真实 A0/parent 导入”正向形状。

## 3. 无副作用调和

operation `00001300`（stdout SHA256 `c3757dafd06a138f9744a03721c22e5d36ab96de7072e3ae6a02a290348a834c`）确认：

- `Rollout_logs/deepcoder_model_smoke_v1` 仍不存在；
- 因而没有 `receipts/plan.json`、SFT/recovery started 或 final receipt；
- 无需删除状态，也不存在 started 未封口或 `requires_audit` 后重跑；
- 没有运行 `execute`，没有 base/child 服务、SFT、DDP ranks、merge、reload、短生成或 recovery；
- source-run 和外部资产没有被该 plan 写入；
- 五轮 output/run 仍不存在，未发生任何五轮。

没有真实 training metrics/rank records/checkpoint/service registry 可供独立真实证据审阅；本报告只回源审阅了阻断点、生产 symlink 创建逻辑和调和证据，不把离线正向 mock 当作真实 MODEL pass。

## 4. occupier 与 cleanup

因为 plan 在 GPU 使用前阻断，Task 5 从未向 occupier 发信号，也无需重启。最终 operation `00001303`（stdout SHA256 `3c292c4035890d951ac2334db161b20c1884b529525cd81040114265b34002a5`）逐项确认：

- identity：owner `dev4-rsiflow-idle-occupier`，PID=PGID=`22389`，start_ticks=`2825714174`；
- 精确 argv：`/data/wuxuecheng/miniforge3/bin/python3.13`、`.../bin/torchrun --nproc_per_node=4 train.py --mem-mb 2500 --log-interval 20`；
- 四卡 0–3 各约 3085 MiB、50–53% util，owner/GPU 状态清楚且保持原状；
- 没有 Task 5 owned jobs/services/training 需要清理。

一次诊断断言曾错误假定 argv 只有四项（operation `00001301`，exit 1），随后只读打印实际 argv 并以完整八项复核通过；该操作员断言失败没有发出信号或改变 owner 状态。

交付后又尝试把 `nvidia-smi` compute PID 直接映射到当前 `/proc`，并假定容器内能看到五个同 PGID members；两个附加断言分别因 PID 不可见和只见 leader 而失败（operations `00001312`/`00001313`）。这些只是可选交叉映射假设不成立，均未发信号或改变状态；最终 operation `00001314` 仅断言可直接证实的事实并成功：leader identity/完整 argv 不变、NVIDIA 报告四个 compute apps、四卡约 3085 MiB 和 43–50% util、source `meta` 保留、MODEL/五轮 output 不存在。stdout SHA256 `8134f10aaf02e5559b381557ce4e22347ab7bf903c008f4a0aee98c5c649ad56`。后续停止/恢复仍必须按 SOP 对实际可见 identity 有界调和，不能使用本次失败的跨命名空间假设代替核验。

关键结果：`00001305-task5_model_smoke_blocker`。

## 5. 解除 blocker 的必要条件

需要 Task 4 后续代码修复和新的 scoped re-review，不能由 Task 5 报告绕过：

1. 用生产真实形状先写失败测试：source-run 含受控集中 `meta` symlink，同时保留任意/未知 symlink 的拒绝反例。
2. 明确定义 canonical source seal：哪些集中 Meta 链接可解引用并独立封印，哪些训练/provenance 文件必须仍位于无 symlink 的 source tree；不得简单忽略所有 symlink。
3. plan/execute 前后都重验该 seal，保持 source+seal identity、外部 A0 inputs 和输出隔离合同。
4. scoped 与完整离线套件通过后重新独立复核，再在同一个仍不存在的 output root 上运行 plan；随后才允许真实 execute 和完整 GPU/cleanup 验收。


---

# Retry 1：reviewed `e849324` 真实 plan 重试

- 日期：2026-11-18（UTC）
- reviewed/execution HEAD：`e849324c348ca357abd065fc5e8e80b4c9b44238`
- 结论：**BLOCKED；原 central `meta` symlink blocker 已越过，但 source parent 的全行 transport-call 资格门拒绝，execute 仍未运行。**

Task 4 最后两轮 scoped re-review 已读：round 2 精确接受并封印生产 central `meta` link，但发现 central target/owner 输出隔离 Important；round 3 在 `e849324` 关闭该 Important，新增 Critical/Important 均为 0，允许经既有 GO 重试。实现者在该 HEAD 报告完整 `608 passed, 2 skipped`、scoped `168 passed`；本次 retry 没有重跑或冒领这些测试。

## Retry 只读前置与 plan

operation `00001318`（stdout SHA256 `8dd3b3c41f45df6f4fccae96fdcb27624b73239490d68b01fc8b47d826dd22e1`）确认 dev4 HEAD/worktree clean、原 MODEL output 与五轮 output 不存在、source root 非 symlink、central `meta` target/owner 精确保持生产形状，且 occupier 继续以原 identity/完整 argv 运行，四卡约 3085 MiB、50–53% util。未提前停止 occupier。

随后在相同 output root、显式 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1` 下执行一次真实 `plan`。operation `00001319`，exit 1，stdout SHA256 `496b5da8a2ec45146fb615fc8e597ac55f33a9c42413161c915a536decf521a6`：

```text
Parent trajectory has no real transport call: deepcoder_taco:deepcoder-taco-2865
```

plan 仍在 `root.mkdir()` 前失败；没有第二个 output root、没有 execute 或重试删除状态。

## Source 事实与判定

operation `00001320`（stdout SHA256 `7992ab350f75d2e3ea5fa48244bd6d50be1dd91eb6f1dbabaef23e91253ccbb6`）只读复核 `round_1/parent/train_trajectories.jsonl`：

- 精确 16 rows、总 `model_call_count=51`；自然正例为 `deepcoder-taco-5475`、`deepcoder-taco-879`、`deepcoder-taco-2510` 三条。
- 九条 negative rows 的 `model_call_count=0` 且 `transport_calls=[]`，包括首个触发拒绝的 `deepcoder-taco-2865`。
- 九条均为 `verification.status=completed`、`success=false`、`full_verifier=false`、`infrastructure_error=false`、`execution_error_type=candidate_error`。
- 公开 `deepcoder-canary-real-results.md` 独立记录同一事实：parent 51 calls、7 个有效 submission、9 个零调用过程失败，其中 6×EAGAIN/BlockingIOError、3×ENOENT/FileNotFoundError；协议保留其在 16 条分母中，没有事后改分。

因此自然正例确实存在，但当前 `_validate_source()` 在逐行 provenance 检查中要求**所有 16 条 parent rows** 至少一次真实 transport call，而不仅是将进入 SFT 的 positive rows。该 source 不满足当前 importer 合同。不得换 source、删除九条负例、改历史 receipt，或为训练绕过这一门。继续需要 controller/Task 4 明确决定：提供符合现有合同的授权 source，或以 TDD/scoped review 定义并验证只约束实际训练正例的 provenance 合同；本次 Task 5 不自行放宽。

## Retry 无副作用与 occupier

同一 operation `00001320` 确认原 MODEL output root 仍不存在，因而没有 plan/started/final receipt； occupier PID=PGID `22389`、start_ticks `2825714174` 和完整 argv 不变，四卡约 3085 MiB、48–50% util。没有 GPU/服务/训练副作用，没有执行 occupier TERM/KILL，也无需恢复。五轮未启动。

关键结果：`00001321-task5_model_retry_zero_call_blocker`。


---

# Retry 3：reviewed `8026c8b` 的 A0 Task identity 阻断

- 日期：2026-11-18（UTC）
- reviewed/execution HEAD：`8026c8b7798b75b9603429d9442db11623a7fe0e`
- 结论：**BLOCKED at plan；execute/SFT/recovery 未运行。此前 central-meta 和 zero-call-negative 两个 plan 门均已越过。**

Task 4 最新 round 4 scoped re-review 已读：零调用豁免仅限完整绑定的 negative，positive transport/full-verifier、16-row 分母、receipt/manifest/A0 binding 和生产 positive selector 均保留，新增 Critical/Important 为 0。实现者报告完整 `617 passed, 2 skipped`、scoped `177 passed`；本次未重跑。

## 前置、操作员错误与真实 plan

operation `00001325`（stdout SHA256 `68c266f17a513d32d4cbacd3e095d7adddb7c2ed9fe4bce15676cf2b9fea93c9`）确认 dev4 HEAD/clean diff、MODEL/五轮 output absent、生产 source central-meta owner、occupier 原 identity/argv 与四卡负载。

第一次 plan 命令在本机误写重复 `-i`，SSH exit 255，未建立远端执行（operation `00001326`，stderr SHA256 `64672654f94fc26fca22bce5141f28b4d1db4cb57768c2403f864006b6862a5a`）；没有创建 output 或影响 occupier。修正后仅执行一次真实远端 plan，显式 bwrap backend，同一 output root。operation `00001327` exit 1，stdout SHA256 `259401b68a474db01b3f1f8163e7413a7d251a19958c19078b378a139de84071`：

```text
Parent trajectory is not bound to the A0 Task: deepcoder_taco:deepcoder-taco-2865
```

失败仍位于 `root.mkdir()` 前，无 plan receipt/started/final 状态，未执行 execute。

## 已确认 mismatch 与未确认根因

operation `00001328`（stdout SHA256 `4aa354b03f940c3d89361766e6c1a15a2fa28e045b0bb1dfa402a4a5de1c7925`）逐行比较生产 `task_hash()`、model ref、Harness bytes 和 artifacts：

- **16/16 rows** 的 `state_hash` 都为 `a2eaba763baf1827e86520a6180f6c476a64534bca2e075870372dfde8ef93e6`；canonical `snapshots/A0/task/task_state_snapshot.json` 当前重算为 `3a395baf3e79e15ce7b2a9ff65906bc791a36e1ff4ab07fbe6dc5be283f72e40`。
- 唯一逐行 mismatch 是 `state_hash`；`model_ref`、`harness_sha256=e41b5c…`、`artifact_input_manifest=[]` 均与 A0 一致。
- 因而这不是单个 zero-call negative 的豁免漏洞，三条 positive 也同样不匹配。

后续只读诊断确认：`artifacts.directory=null`，当前与存储 manifest 都是 `[]`，不是 artifacts 漂移（`00001329`，SHA256 `7ce287babce9ddea9d61e19c0794aa3998673da45b472f5c8f2a52bfcb48f1bd`）。把 Harness path 单独替换为原 seed path 会得到第三个 hash `daa77c…`，并不解释 row hash（`00001330`）。使用 source 所属 preflight runtime 重算 canonical snapshot 仍为 `3a395…`（`00001331`），所以也不是 five-round 与 preflight 当前 `task_hash` 实现差异。历史 rollout state 与 snapshot 的精确序列化/path 差异仍待 Task 4 回源，不在本报告中猜定。

不得把 row hash 改成 snapshot hash、选择其他 A0 或跳过 identity 校验。继续需要 production-shape TDD 和 scoped re-review，明确并恢复可证明的 A0/parent Task identity。

## 无副作用与 occupier

operation `00001328` 同时确认原 MODEL output root 仍不存在；occupier PID=PGID `22389`、start_ticks `2825714174` 和完整 argv 不变，四卡约 3085 MiB、49–51% util。未发 occupier 信号，无 services/training/GPU 副作用，无需恢复；五轮未启动。关键结果：`00001332-task5_model_retry3_a0_state_hash_blocker`。
