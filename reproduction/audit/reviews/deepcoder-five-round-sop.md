# DeepCoder 单域五轮工程演化 SOP

- 版本：设计基线 v1，2026-11-18（UTC）；launch-hardening 收口 v2
- 适用范围：`deepcoder_five_round_canary`，固定同一批 16 条 function，A0+B1–B5
- 前置状态：Tasks 1–4 实现和 scoped re-review 已完成；Task 5 MODEL **v2 工程 PASS**（efba18d 实测四 rank/one epoch/merge/reload/两次生成与 retrained=false recovery；Spec PASS / quality DONE_WITH_CONCERNS / engineering PASS），**v1 历史失败保留**（首次 source-run 集中 `meta` symlink 阻断）。汇总器离线消费者 fix4（d951185）经 Qwen r4 scoped re-review PASS。launch-hardening 已实现参数化 supervisor（`reproduction/scripts/deepcoder_five_round_supervisor.py`）与唯一 external cleanup writer（`reproduction/scripts/reconcile_deepcoder_run.py`），离线生命周期测试 `RSIFlow/tests/test_five_round_lifecycle.py` 通过；可执行命令见 `deepcoder-five-round-runbook.md`。**正式五轮 execute、独立 benchmark、694/744 均未运行**；MODEL smoke 已训练不等于正式实验已跑。
- 设计规格：`docs/superpowers/specs/2026-11-18-deepcoder-five-round-canary-design.md`
- 操作 runbook：`deepcoder-five-round-runbook.md`（唯一五轮元组、final launch seal、两阶段 dev4 preflight、launch/monitor/stop/resume/cleanup/restore 完整命令）

本 SOP 规定操作顺序和判定门。具体可执行命令绑定最终审阅 HEAD 和机器配置，见 `deepcoder-five-round-runbook.md`（参数化 supervisor/reconcile 脚本）；不得拿单轮 canary 配置直接改 `rounds=5` 启动，不得任意覆盖 `--run-dir`。

## 1. 角色与状态定义

- Controller：维护代码、配置、审计、supervision 和公开结果，不代替 Astra 选择组件。
- Task agent：本地四卡 Qwen3-4B 与当前 Harness/artifacts 组成的执行体。
- Meta agent：固定 Astra，通过同一持久 thread 分析轨迹、构造候选、作出选择和沉淀经验。
- 实验 attempt：某轮 `attempt_1`；五轮每轮最多一个。
- Supervision attempt：launcher 的一次进程监督，编号随恢复递增，与实验 attempt 不是同一概念。
- Round terminal：当前轮 accept 或 retain 后，comparison、skills/context/snapshot 完整。
- Run complete：A0+B1–B5 全部 terminal，finish gate 通过，jobs/app-server/services/GPU 分别调和。

`retain_parent` 不是 run complete，也不是失败；它是当前轮保留父代的有效终态，然后进入下一轮。

## 2. 不可变边界

1. 已完成的单轮 canary run 不重启、不覆盖、不导入为新 A0 active Task。
2. 新五轮使用独立 output/run、空技能种子和初始 Qwen/seed Harness。
3. 显式使用 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`，不得回退 host、Landlock 或 danger-full-access。
4. 不放宽 host workspace 写门，不把凭据写入仓库、网页、命令行或日志。
5. 固定 16 条任务的 ID、顺序和 source hashes 在五轮中保持一致；不得根据结果换题。
6. 不运行独立 validation、694/744、五域任务或作者结果对比。
7. 每轮最多一个实验 attempt，不通过重试制造正增益。
8. 不修改官方 verifier、冻结原始记录或评分规则来制造通过。

## 3. 阶段 A：文档、实现和离线门

按顺序完成：

1. 归档单轮专项复盘和本 SOP。
2. 实现独立五轮 scope、重复批次 release、逐轮 retain 推进和一致的 prompt/resume 指令。
3. 以 TDD 覆盖配置拒绝矩阵、五轮进度、accept/retain、B5 finish 和原协议回归。
4. 实现隔离的多轮恢复探针，验证同 thread resume 不重复 A0、rollout、候选或 SFT。
5. 运行完整离线套件、JSON 解析和网站契约测试。
6. 独立审阅全部 Critical/Important 关闭后，才允许进入真实环境门。

任何 RED、未审阅生产改动或旧 scope 行为变化都阻止继续。

## 4. 阶段 B：MODEL 独立真实 smoke

该 smoke 在五轮 run 外执行，输出、服务和审计均独立。

### 4.1 dev4 occupier 安全交接（所有真实 RSIFlow GPU 工作通用）

dev4 空闲时由 `/data/wuxuecheng/z-occupier-ready` 占卡。真实 MODEL/五轮使用 GPU 前必须完成以下交接；不得仅凭 `nvidia-smi` 或端口猜 owner：

1. 读取共享 `/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json`，保留其中的 `pid/pgid/start_ticks/owner/gpus`。当前 owner 为 `dev4-rsiflow-idle-occupier`，目标 GPU 必须精确为 0–3；PID 和 start_ticks 每次从实时 identity 读取，不写死。
2. 在 dev4 重新读取 `/proc/<pid>/stat` 和 `cmdline`，逐项要求 PID=PGID、start_ticks 与 identity 相同，并核对完整 argv。当前已实测的精确 argv 为：
   `/data/wuxuecheng/miniforge3/bin/python3.13 /data/wuxuecheng/miniforge3/bin/torchrun --nproc_per_node=4 train.py --mem-mb 2500 --log-interval 20`。
   任一项不符、进程不可读或 owner 不明确都停止，不发信号。
3. 先快照该 PGID 全部非 zombie members 的 PID/start_ticks，再只向已核验的 occupier PGID 发一次 TERM；有界等待并周期性重读 `/proc`。禁止 `pkill`、按端口 kill、按宽泛进程名 kill 或对其他 PGID 发信号。
4. 等待后仍有 members 时，只能对重新核验“原快照 PID + 同 PGID + 同 start_ticks”的剩余 occupier 进程逐个 KILL；新 PID、start_ticks 不符或读取不确定均不发 KILL，转为 blocker。再次调和至无同 identity members。
5. 用 `nvidia-smi --query-compute-apps` 确认无计算进程，并确认 GPU 0–3 显存释放、四端口可用；然后才允许 MODEL execute 或五轮启动。释放 occupier 不等于 RSIFlow cleanup 已完成，二者分别记录。

smoke/五轮结束或失败后的 owned jobs/services/training 清理和四卡释放必须先按各自 registry/identity 完成。只有 owner 与 GPU 状态都明确时才恢复 occupier：

1. 在 `/data/wuxuecheng/z-occupier-ready` 使用 `nohup setsid env CUDA_VISIBLE_DEVICES=0,1,2,3 NPROC=4 ./run.sh --mem-mb 2500 --log-interval 20`，stdin 重定向 `/dev/null`，日志追加到 `runtime/dev4/occupier.log`；记录新 `$!`，不得假定旧 PID 可复用。
2. 有界等待 `run.sh` exec 为完整 torchrun argv，复核新 PID=PGID 和 `/proc` start_ticks 后，以临时文件 + 原子 rename 写新 `identity.json`（owner、pid、pgid、start_ticks、gpus 0–3）。不要在 exec/argv 尚未稳定时写 identity。
3. 实测四卡均有 compute apps、显存和 util 负载，并检查 occupier log 的四个 ranks；仅进程存在不算恢复成功。若 owner/GPU 状态不明确或四卡未证实，不贸然重复启动，保留现场并报告 blocker。

2026-11-18 Task 5 的**首次（v1，历史）**MODEL plan 在 GPU 交接前被 source `meta` symlink 阻断，所以没有停止 occupier；原 identity 和四卡负载已复核不变。该 v1 失败保留为历史；**v2 工程 PASS**（efba18d）已按上述完整交接实测通过。任何后续真实重试仍须执行上述完整交接。

### 4.2 MODEL 顺序

1. 从上一轮父代自然成功轨迹中选择正例，逐条核验源 run、task ID、record hash、Task identity 和 trajectory hash。
2. 只使用这些自然成功样本，执行 one-epoch、四-rank、positive-only LoRA SFT。
3. 合并并记录新 checkpoint/weights identity。
4. 在四卡上 reload child，完成一次短生成和身份绑定检查。
5. 停止 owned services，核验 jobs、进程和四卡释放。
6. 将 smoke 标为 `engineering_only`，不进入五轮 accept/skills，不宣称模型提升。

若任一 provenance、训练、merge、reload 或清理步骤失败，MODEL 门不通过。不得使用 held-out、人工造标签或上一轮候选输出替代自然正例，也不得让正式五轮静默走未验证 MODEL 路径。

## 5. 阶段 C：真实启动前门

在 dev4 fresh shell 中按最终 runbook 逐项检查：

- reviewed HEAD 与执行 worktree一致，tracked/cached diff 符合发布要求；
- 新 config 的 output/run 自然派生且位于授权 workspace；
- 新 run dir 不存在，历史 run 保持不变；
- frozen release 明确 `round_repeated`、5×16 位置和 16 unique IDs；
- pinned bwrap binary hash 与 install receipt 匹配；
- Meta 凭据仅从既有私有 env 注入并检查存在，不读取或打印；
- Astra Responses 健康探针使用固定 provider/model；
- GPU 0–3 为预期 A800、无计算进程，四端口和 socket 可用；
- 磁盘、完整测试、离线多轮预检和 MODEL smoke 均有最终证据；
- `launch.py --config <新配置> --check` 的 run dir、rounds=5、16/round、空技能种子和资产路径全部匹配。

任一项失败即停止，不创建真实 run 来试错。

## 6. 阶段 D：detached 启动

只允许最终审阅后的参数化 detached supervisor 入口（`deepcoder_five_round_supervisor.py launch`，命令见 runbook §4）：

1. exclusive 创建 supervision `0001` 及独立日志目录（拒绝复用旧 exit/reconcile/identity/stop-requested）。
2. 用 `Popen(start_new_session=True)` + 父子 handshake（child 先原子写 identity 再跑前台命令），避免 nohup/exec race；child 复用 `timeout --foreground --signal=TERM --kill-after=5m 4h` 算法。不向 launcher 传 `--detach`，不加 `--prepare-data`。
3. 原子记录 PID=PGID、start_ticks、argv、run dir 和 attempt dir，并生成 `kind=rsiflow_launch` seal（config/base/effective hashes、owners、expected_gpus）。
4. monitor 只读；Ctrl+C 只退出 monitor，不停止实验。
5. launcher 到时只代表其监督边界触发，durable jobs/app-server/services 仍需独立调和。

禁止另写 foreground 备选、使用 `timeout ... --detach` 或恢复旧的 `timeout --foreground ... | tee` 操作员 PGID 方案。

## 7. 阶段 E：A0 与每轮核验

### A0

确认 bootstrap 一次、初始 Task/Meta snapshot 一次、空技能种子、初始 checkpoint/Harness identity 正确，无 baseline validation。

### B1–B5 每一轮

按以下顺序检查，缺一项就不把该轮记为 terminal：

1. 当前 active parent 与上一轮 selected Task 一致。
2. 父代完整覆盖 16 IDs，manifest 和 round identity 正确。
3. Meta evidence、decision、skill_use 和唯一 `attempt_1` 存在。
4. 实际组件与 candidate state 一致；HARNESS 检查不超过三次。
5. MODEL 仅在 smoke 已通过且当前父代有自然正例时运行，训练 provenance 完整。
6. 候选在相同 16 IDs 上运行，比较和逐任务 differences 完整。
7. 接受必须有严格净正增益和 Meta 显式选择；retain 不改变 active parent。
8. 记录真实 calls、有效 submission、full verifier、success、delta 和逐任务转移。
9. 单独分析零调用 EAGAIN/ENOENT 等过程异常及其对转移的影响。
10. skills/context/snapshot 落盘，snapshot 内 parent/candidate/selected hashes 可复算。
11. accept 后 active=candidate；retain 后 active=parent；随后才进入下一轮。

每轮结束后向操作日志追加脱敏摘要，不等五轮结束后凭记忆补写。

## 8. 阶段 F：异常、停止和恢复

### Provider 502 或 transport 中断

1. 记录 distinct failed turns 和 error events，避免重复计数。
2. 保留最近完成的 milestone 和 durable job 回执。
3. 持续失败时按当前 attempt identity 停止 launcher，完成 reconcile 和必要 cleanup。
4. 健康探针恢复后，使用同一 run/config/backend/thread 和新 supervision attempt 恢复。
5. 从下一 milestone 继续，不重复 A0、父代 rollout、已验证候选或 SFT。

### 写门、bwrap 或身份错误

立即 fail closed。不得通过关闭校验、扩大可写根或回退 host 来继续。修复需新代码审阅和预检，历史 attempt 保留。

### Active durable job 未结束

等待自然最终回执，或由既有 owner 使用生产 job_status/wait_job 和 start identity 调和。不得发明 kill 命令，不得新 resume，不得记 cleanup completed。

### 结果异常但流程仍运行

不事后改分或换题。记录协议事实与质量限制；若 ID/hash、进度或 active identity 不一致，则停止当前 attempt 并排障。

## 9. 阶段 G：最终完成与清理

最终完成必须同时核验：

- `state.status=complete`、`last_turn_status=completed`；
- A0+B1–B5 milestones 完整，`current_stage=COMPLETE`、`finish_allowed=true`；
- B5 前没有提前 finish；
- 每轮只有一个实验 attempt，active state 链与逐轮选择一致；
- 所有 durable jobs 有最终结果；
- 当前 supervision exit 和 reconcile 完整；
- launcher/Codex PID 不存活；
- owned registry 为 services_stopped，GPU 0–3 无计算进程。

任何一项缺失都只能报告实际完成轮次和 blocker。

清理必须使用唯一 external cleanup writer（`reconcile_deepcoder_run.py cleanup`，命令见 runbook §6.2），重新验证 config/output/run/attempt 的绝对路径与身份，实际测量 owner/GPU 后原子写新 observation（不覆盖历史）。不得按端口或进程名宽泛 kill，不删除日志、receipts 或历史 attempts。缺证据只能 pending/failed；protocol complete 与 run complete 分离，cleanup completed 不自动升级协议未完成为成功。

## 10. 阶段 H：报告与公开

最终报告包含逐轮表格和跨轮 active/skills 链，明确：

- 每轮组件、accept/retain、分数和回归；
- MODEL/SFT 哪些轮实际发生，哪些未发生；
- 固定批净变化不等于因果或泛化；
- 过程异常和零调用对结果的限制；
- 五轮工程完成不等于五域、3000 任务、694/744 或作者复现。

更新 `PROGRESS.md`、`LEARNING.md`、`RETROSPECTIVE.md`、AGENTS、复现手册和网页 JSON 时同步口径。公开前运行完整测试、网站测试、JSON/JS 检查和密钥扫描；只发布脱敏汇总。

## 11. 审计纪律

- 主 checkout 是唯一权威审计根，但既有 dirty 文件不得被覆盖或拼接。
- 每条命令、文件变更、网络/SSH、启动、停止、恢复和清理使用唯一 phase 记录。
- shell 使用 `audit_exec.py`；其他工具操作由 `record_operation.py` 记录脱敏输入和证据。
- 关键结果必须有可复算 evidence path/hash，agent 自述不能替代主 agent 复核。
- 失败、拒绝、超时和无结论同样入账；旧记录不回写美化。
- cpolar 只暴露 `web/`，原始日志和私有轨迹始终位于公开根之外。
