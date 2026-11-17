# dev4 persistent Meta `pidfd_open` ENOSYS 根因与兼容修复

> 记录日期：2026-11-17。本文永久记录 DeepCoder function 16 canary 真实 supervision attempt `0001` 的基础设施失败及后续离线修复；不记录动态 PID、TCP 入口或凭据。

## 1. 真实 attempt 0001 根因

真实 canary 使用显式 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1` 和 detached supervisor 启动，唯一 supervision attempt 为 `0001`。运行在 dev4 的 Linux 4.18 环境中，x86_64 `pidfd_open` syscall 434 的最小探针明确返回 `ENOSYS`（errno 38，`Function not implemented`）。

生产 `PersistentMetaSandbox._track_process` 当时在启动 Meta app-server 后无条件使用 `pidfd_open` 跟踪进程，因此在 A0/bootstrap 前失败。launcher 记录 `Meta connection interrupted: OSError: [Errno 38] Function not implemented`，没有建立 Meta thread，没有选择 HARNESS/MODEL/ARTIFACTS，没有父代 rollout、候选、SFT、accept 或 retain；该 attempt 不是有效 canary 结果。

初始 app-server state 直到连接建立后才写 `sandbox_backend`。上述启动异常留下的 state 为 `status=disconnected`、`thread_id=null`，但缺少 `sandbox_backend`；自动重连将缺字段解释为 `legacy_host`，与明确选择的 `linux_bwrap_seccomp_v1` 不一致，后续重连因此继续 fail closed。原始真实证据见同目录 `deepcoder-canary-runbook.md` 所引用的运行记录及 SDD `task-5-report.md`；历史日志和 state 不应为绕过该错误而被改写。

## 2. 修复提交

1. `8241eede6f508c9b08c4d4049714373ef937245e`（`fix: 兼容 persistent Meta pidfd ENOSYS`）
   - 为旧内核增加 ENOSYS-only `/proc` 后备。
   - state 在 `server.start()` 前写入所选 backend，并在启动异常后保留。
2. `eef0a50e11b8c738d0b56f5021fdf1a9a683483e`（`fix: 收紧旧内核进程身份核验`）
   - 收紧 `/proc` 读取/解析错误分类。
   - 补齐 leader/member 生命周期、TERM→KILL、post-signal 新成员、旧 state 和 identity 清理测试。

## 3. 当前兼容语义

正常支持 pidfd 的内核继续使用原 pidfd 路径。只有 leader `_pidfd_open` 返回 `ENOSYS` 时才建立后备；EPERM 等其他 errno 不进入后备，不回退 host、Landlock 或 `danger-full-access`。

后备基于 `spawn(start_new_session=True)` 形成的 owned PGID，要求 leader `PGID == PID`，并记录 `/proc` start_ticks。信号前执行 group 双快照和逐 PID PGID/start_ticks 核验，只向已核验的具体 PID 发 `os.kill`；不使用无条件 `killpg` 或宽泛 `pkill`。leader 已退出但 descendants 仍存活时仍可按已记录 identity 清理。正常 close 的 TERM、等待、必要 KILL 和 leader reaping 语义保留，pidfd 与后备 identity 均清理。

只有明确 `ENOENT` 和 zombie 被视为确认退出；其他 `OSError`、缺字段 `IndexError`、非法 PGID `ValueError` 等读取/解析错误向上传播，group scan 不静默忽略未知错误。信号循环前完整构造 targets，普通 member identity 变化时零目标；确认退出和 `os.kill` ESRCH 按明确退出竞态处理。

state 对 bwrap 记录 `linux_bwrap_seccomp_v1`，对 legacy 记录 `legacy_host`，并在 `server.start()` 前落盘。旧 app-server state 缺 backend 且本次选择 bwrap 时仍按 `legacy_host` mismatch 拒绝，原 state 不改，server/sandbox 不构造；不自动迁移旧失败 run。

## 4. 离线测试与独立审查

实现者在 Round 1 最终代码上运行完整离线套件：

```text
516 passed, 2 skipped in 96.00s
```

skipped 不计为通过。相关两个完整测试文件为 `68 passed`；ENOSYS 选择器为 11 个通过，其中 10 个是后备用例，1 个是非 ENOSYS 门控用例。正常 pidfd 回归不计入后备覆盖。测试包括真实无害进程的信号/reaping、leader 先退出、真实 TERM→KILL，以及精确 mock 时序的 member identity、退出/ESRCH 和 post-signal 新成员分支。

Round 1 scoped re-review 对 `8241eed..eef0a50` 给出：

- Spec compliance：**PASS（scoped）**。
- Task quality：**PASS（scoped）**。
- 新 Critical/Important/Minor：本轮限定范围内均无。

该审查只静态复核上一轮 2 Important + 2 Minor，未重跑测试，也不构成 dev4 Linux 4.18 真实应用链路或真实 canary 通过结论。详细证据见 SDD `task-5-infra-fix-report.md` 与 `task-5-infra-fix-rereview.md`。

## 5. 保留风险与真实验证边界

- `/proc` 最后 identity 检查到 `os.kill` 之间仍存在理论 PID 复用窗口；双快照和 start_ticks 核验不能宣称达到 pidfd 等价保证。
- 信号循环不是原子的。post-check 可能在部分 TERM 已发出后发现新成员或歧义并抛出异常；`close()` 的 finally 会清空追踪状态，同一对象不能据此重试。fail closed 不等于零信号或完整清理。
- 上述失败可能留下 owned processes，必须按 runbook 使用记录的 PGID/start identity 做外部 identity-checked 调和/清理，不得改用宽泛 `pkill` 或按端口杀进程。
- 后续实测已完成：修复后的 dev4 Linux 4.18 真实 app-server 成功启动并保持同一 Astra thread，supervision 0002 从 `candidate_built` 恢复，完整 16-task HARNESS canary 完成 accept、state COMPLETE、9 个 durable jobs 最终回执和四卡 cleanup。证据见 `deepcoder-canary-real-results.md` 第 1、7 节；这些后续事实不回写或覆盖历史 attempt，也不消除上述理论竞态边界。
