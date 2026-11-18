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
