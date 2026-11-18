# DeepCoder 单域五轮真实启动/监控/停止/恢复/清理 Runbook

- 版本：v1，2026-11-18（UTC），绑定 launch-hardening 实现
- 适用：`deepcoder_five_round_canary`，A0+B1–B5，固定同一批 16 条 function
- 参数化脚本：`reproduction/scripts/deepcoder_five_round_supervisor.py`（launch/status/stop）、`reproduction/scripts/reconcile_deepcoder_run.py`（inspect/cleanup，唯一 external cleanup receipt writer）
- 消费者：`reproduction/scripts/summarize_deepcoder_five_round.py`（`_validate_cleanup` 与 writer 共用同一 schema）
- 设计规格：`docs/superpowers/specs/2026-11-18-deepcoder-five-round-canary-design.md`；SOP：`deepcoder-five-round-sop.md`
- 离线生命周期测试：`RSIFlow/tests/test_five_round_lifecycle.py`（临时 config/run/无 GPU 受控进程；不触真实 launcher/GPU/API）

> 本 runbook 是操作合同，不是已执行记录。所有真实启动/停止/cleanup 必须由有运行授权的主 agent 在 dev4 实际测量后执行；离线测试只验证参数迁移与拒绝门，不冒充现场。旧单轮 canary 路径**绝不**作为五轮可执行目标。

> **fix4 实现状态**：启动证据使用单向不可变链；源文件/registry 保存采集时快照；运行身份、GPU raw/parsed、job/phase provenance 与 restore 资源校验均 fail closed。同一 run 的 143→0002、生产 PersistentMeta/ControllerTools/TaskAdapter 与 writer→consumer 联合测试见 fix4 报告。以下命令供授权后的 dev4 实际执行；实现和离线测试不等于已进行现场 preflight 或五轮。

## 0. 唯一五轮目标元组（从 config 自然派生，禁止任意 --run-dir 覆盖）

```bash
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
CONFIG="$ROOT/reproduction/configs/train_4b_deepcoder_five_round_workspace.json"
OUTPUT_ROOT="$ROOT/Rollout_logs/deepcoder_five_round_16_v1"
RUN_DIR="$OUTPUT_ROOT/runs/deepcoder_function_16_five_round_workspace_v1"
CENTRAL="$ROOT/Meta_logs/deepcoder_function_16_five_round_workspace_v1"
RELEASE="$ROOT/reproduction/logs/deepcoder-five-round-16-v1/release"
READY="$ROOT/reproduction/logs/deepcoder-five-round-16-v1/READY.json"
REGISTRY="$OUTPUT_ROOT/services/four_gpu_services.json"
```

每段（launch/monitor/stop/resume/cleanup/restore）都必须独立重设并校验该元组与 config hash；supervisor/reconcile 内部从 `CONFIG.output_root/runs/run_name` 派生 run，拒绝任意覆盖。恢复 policy 精确为 `scope=deepcoder_five_round_canary / rounds=5 / evaluation_schedule=none / retry_rejected_from_round=6`，不得删除既有 policy 让其重新生成。

## 1. dev4 固定入口与 shell 纪律

- dev4 固定 TCP 入口：`1.tcp.vip.cpolar.top:11356` → `127.0.0.1:22`（通用 `/data/wuxuecheng/CPOLAR_TCP_SOP.md`）。SSH 数组 `DEV4_SSH` 按该 SOP 设置；入口变化以实测为准，不写死历史动态地址。
- 远端命令一律用 **bash**（`bash -s` 或 `bash -c`），声明 `set -euo pipefail` 与必要 PATH/解释器。本工作根默认 shell=zsh，`${!name}` 等 Bash 间接展开不得复制到 zsh 半执行。
- 显式 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`；不回退 host/Landlock/danger-full-access。凭据仅从既有私有 env 安全注入，不读取/打印/写仓库。
- 主审计根为主 checkout；worktree 账本不拼接。每条命令用 `audit_exec.py`/`record_operation.py` 记录脱敏证据。

## 2. 阶段一 preflight（occupier 仍运行时，dev4 执行并写主审计）

逐项检查并封印 final launch seal（由 supervisor 在启动前生成 `kind=rsiflow_launch`）：

1. reviewed HEAD 与执行 worktree 一致，tracked/cached diff 符合发布；完整/专项测试证据 hash。
2. fresh paths：`OUTPUT_ROOT/RUN_DIR/CENTRAL` 全不存在（含 dangling symlink）；历史单轮 run 保持不变。
3. config/base/effective config hash（`TaskAdapter._config().model_dump()`）；五份 manifest+payload/READY；seed Harness/Qwen weights manifest；pinned Codex/bwrap/install receipt+manifest+launcher hash；Task 5 review/回执 hash。
4. SSH/host/namespace 身份、boot_id、PID namespace；私有 env 只安全注入与非空检查。
5. 固定 provider/model（gpt-6-astra）Responses 200/completed 健康探针（不泄露 key）。
6. 四卡型号/UUID/显存（A800）、端口 8271–8274 bind-close、有效 sockets（`/tmp/rsiflow_4b_meta_worker.sock`、`...relay.sock`，以及 Persistent Meta private relay `/tmp/rsiflow-meta-relay-*/transport.sock`）、磁盘/inodes。
7. 离线与 MODEL 证据：MODEL v2 工程 PASS（efba18d 实测四 rank/one epoch/merge/reload），v1 历史失败保留；若仅汇总器/文档变化且生产 hashes 不变可引用原证据，不要求无意义重训。

任一项失败即停止，不创建真实 run 试错。`launch.py --config "$CONFIG" --check` 的 run dir/rounds=5/16/round/空技能种子/资产路径全部匹配。

### 2.1 两阶段 preflight receipt 与 prelaunch seal 的生成/传入（R2，唯一命令）

阶段一（occupier 仍运行）与阶段二（停 occupier 后）各产出一份 receipt，阶段二必须引用阶段一 hash；supervisor 真实模式 Popen 前逐项核验 reviewed commit/五份 manifest+payload+READY/seed Harness/model weights/Codex/bwrap install+manifest+launcher/Task5 MODEL 证据/两阶段 receipt paths+hashes/实际 backend，缺/错即实验命令未执行。生成与传入：

```bash
# dev4 bash；先设置 §0 元组。下面路径均为新的审计目录，不能使用真实 run 目录。
set -euo pipefail
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
AUDIT_DIR="$ROOT/reproduction/logs/five-round-launch-preflight-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$AUDIT_DIR"
# 仅在有 API 探针授权时执行；凭据已通过安全环境注入。输出不记录 Authorization/key。
PROVIDER_RESPONSE="$AUDIT_DIR/provider-probe.json"
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" probe-provider \
  --config "$CONFIG" --output "$PROVIDER_RESPONSE"
OCCUPIER_IDENTITY=/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json
STAGE1="$AUDIT_DIR/stage1.json"
STAGE2="$AUDIT_DIR/stage2.json"
PRELAUNCH_SEAL="$AUDIT_DIR/prelaunch-input.json"
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" observe-preflight \
  --config "$CONFIG" --phase stage1 --provider-report "$PROVIDER_RESPONSE" \
  --occupier-identity "$OCCUPIER_IDENTITY" --output "$STAGE1"
# 只在有交接授权时执行。实际 identity-checked 收容在全局资源锁内，写真实 before/after 回执。
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" handoff \
  --stage1 "$STAGE1" --output "$AUDIT_DIR/handoff.json"
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" observe-preflight \
  --config "$CONFIG" --phase stage2 --provider-report "$PROVIDER_RESPONSE" \
  --occupier-identity "$OCCUPIER_IDENTITY" --stage1 "$STAGE1" \
  --stop-receipt "$AUDIT_DIR/handoff.json" --output "$STAGE2"
# 从可信 config/provenance 推导全部资产路径、读取实际 HEAD，不接受调用者缩减资产集合。
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" preflight-input \
  --config "$CONFIG" --stage1 "$STAGE1" --stage2 "$STAGE2" --output "$PRELAUNCH_SEAL"
```

## 3. occupier 安全交接（阶段二，仅在阶段一通过后）

按 SOP §4.1 与共享 identity `/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json`：

1. 实时读取 occupier `pid/pgid/start_ticks/owner/gpus`（owner=dev4-rsiflow-idle-occupier，GPU 0–3），dev4 重读 `/proc/<pid>/stat`+`cmdline` 逐项核验完整 argv；任一不符/不可读/owner 不明 → 停止，不发信号。
2. 快照 PGID 全部非 zombie members → 只向已核验 occupier PGID 发一次 TERM → 有界等待重读 `/proc`；剩余仅对“原快照 PID+同 PGID+同 start_ticks”逐个 KILL；新 PID/start_ticks 不符/读取不确定 → blocker。禁止 pkill/按端口/宽泛 kill。
3. `nvidia-smi --query-compute-apps` 确认无计算进程、GPU 0–3 显存释放、四端口可用。
4. 再次确认无 compute apps、每卡近空闲（launcher 门 memory.used<=1024MiB/util<=5%，连续两次）、端口/socket 无冲突、重验 launch seal 未变 → 允许唯一 detached launch。

occupier 停止与 supervisor 启动/失败启动关联记录；用目标机独占锁（`flock`）串行化 handoff/start/cleanup/restore，与 launcher 自身锁配合。provider/写门/identity 失败发生在停 occupier 之后时，明确持续看护 owner，不 shell 退出失联，不无条件 trap 启动 occupier 与仍活 job/DDP/services 抢卡。

## 4. 唯一 detached launch（supervisor 0001）

```bash
# dev4 fresh bash；元组按 §0 重设并校验
set -euo pipefail
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" launch \
  --config "$CONFIG" --supervision 0001 --timeout 4h --kill-after 5m \
  --preflight-seal "$PRELAUNCH_SEAL"   # R2：真实模式必填，Popen 前核验完整资产/两阶段 receipt
```

- supervisor 用 `Popen(start_new_session=True)` + 父子 handshake（child 先原子写 identity 再跑前台命令），避免 nohup/exec race；child 复用 `timeout --foreground --signal=TERM --kill-after=5m 4h` 算法 + 有界 identity-rechecked PGID TERM/KILL reconcile。
- attempt 目录 exclusive（拒绝复用旧 exit/reconcile/identity/stop-requested）；identity PID=PGID/start_ticks/argv 原子写入并绑定 run/attempt。
- 生成不可变 `prelaunch-seal.json` → `launch.json` → `post-launch-identity.json` → `launch-final.json`；每个后继只引用前驱 hash，writer/consumer 必须消费完整链，缺 runtime 不回退占位 owner/thread/GPU。
- 不向 launcher 传 `--detach`/`--prepare-data`；不另写 foreground 备选或 `timeout ... --detach`。
- **4h 是每个 launcher supervision 预算，不是 jobs/DDP/services 硬边界**；有效 training timeout=604800s（7天）、max_wall_seconds=2592000s（30天）另列。到期不代表实验停止，durable work 按 §6 独立调和。

## 5. 本机只读 monitor

```bash
"$PYTHON" "$ROOT/RSIFlow/monitor_meta.py" --run-dir "$RUN_DIR" --once
"$PYTHON" "$ROOT/RSIFlow/monitor_meta.py" --run-dir "$RUN_DIR" --history 20
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" status --config "$CONFIG" --supervision 0001
"$PYTHON" "$ROOT/reproduction/scripts/summarize_deepcoder_five_round.py" "$RUN_DIR" --config "$CONFIG"
```

Ctrl+C 只退出 monitor，不停实验。每轮按 SOP §7 核验 accept/retain/active 链/真实 calls/verifier/snapshot；逐轮追加脱敏摘要。`launch_requested`/alive/exit 0 均不等于 PASS。

## 6. 停止与清理

### 6.1 手动停止 launcher supervision（只停该 PGID）

```bash
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" stop \
  --config "$CONFIG" --supervision 0001 --grace 90
```

写 stop-requested → identity-checked TERM → 有界等待 exit+complete reconcile（手动停止语义 143；timeout 124/137）。identity 不符/PID reuse/读取不确定 → fail closed，不 pkill/按端口。durable jobs/services/app-server 另 session，不在此停止。

### 6.2 inspect / cleanup（唯一 external cleanup writer）

```bash
"$PYTHON" "$ROOT/reproduction/scripts/reconcile_deepcoder_run.py" inspect \
  --config "$CONFIG" --run-dir "$RUN_DIR" --supervision 0001
"$PYTHON" "$ROOT/reproduction/scripts/reconcile_deepcoder_run.py" cleanup \
  --config "$CONFIG" --run-dir "$RUN_DIR" --supervision 0001
```

writer 顺序（实际测量，不接受 `--completed`/`--stopped`）：
1. lifecycle 锁 + launch seal/config/base/effective/run/attempt/host boot_id/PID namespace 核验；读本 attempt identity/exit/reconcile，缺失/未结束 → 只 inspect，cleanup pending；旧 supervision 逐项封口。
2. 调和非服务 owner：launcher/app-server 全 group、全部 durable jobs/result/turn、全部 gpu_phase/DDP group。**终态 result 存在也核进程树退出**；leader ENOENT 不代表 group 空（DDP rank 活仍 alive）；权限/解析/PID reuse/未归属 → unknown。active jobs/DDP → pending，不 stop_services，不发明 kill；无安全取消时等待自然终态，dead job 缺 final 记 `terminal_failed/requires_audit`，不写 completed/不重跑 SFT。
3. 验证 owned registry（output_root 派生、归属、checkpoint/weights、四卡 records/identity）；非服务 owner 全退出后若仍需停 → 生产 `stop_services(effective_config)`；phase 已 stopped 仍复查组/GPU。
4. 实际测量：registry/hash/phase、逐组 inference/training/job/Meta/launcher 全退出；目标机 `nvidia-smi`（timeout/check=True）GPU0–3 index/UUID/name/memory.total/free/used/util + compute-app PID/UUID；预期四卡、无 compute apps、空闲阈值（连续两次）、端口 bind-close。未知外部占用只 block，不信号清除。
5. 原子封口：每次新 exclusive observation 目录（不覆盖历史 pending/failed/completed），临时文件+fsync+原子 rename；全成功才 completed，服务停止失败=failed，job 活/identity unknown=pending。无 registry 且从未启动服务只能在前置证据齐全时 `not_created_verified`；五轮正常完成应有 owned registry，缺失不降格 stopped。
6. receipt 字段/绑定/hash 按 Codex 方案（schema_version/kind/status/blockers/writer/binding/supervision/protocol/owners/registry/gpu_observations/ports/actions/errors/evidence_files_sha256/occupier）。消费者逻辑：`run_complete = protocol_complete AND launcher_exited AND external_cleanup.completed AND 全部必需终态证据有效`；protocol complete 但 jobs/services pending → “协议完成，外部资源待调和”，不重启 complete run。

## 7. 恢复（同 thread，已完成 SFT 不重做）

恢复前封存旧 thread_id、active Task/hash、policy/config/backend、completed milestones、job/result/request、训练 marker。新 supervisor 用相同 config/run/code/backend，记录 `resumed_from`，启动后验证 thread_id 与旧值精确相同、只有新 supervision 编号变化。不删除 state/codex_home/policy/lock/history，不重放 seed，不复制旧单轮 thread。

分支合同：
- **state=complete**：只做外部 cleanup（§6.2），不 resume。
- **incomplete/disconnected**：允许恢复；从下一 milestone 继续，不重复 A0/父代 rollout/已验证候选/SFT。
- **active job 仍运行**：等待自然终态或既有 owner `job_status`/`wait_job`+start identity 调和；未调和前禁 cleanup completed/新 resume。
- **dead job 缺 result 或 SFT receipt 非终态**：`requires_audit`，不因新 supervision 编号再调用 sft。
- **metrics/checkpoint 已完成但候选/技能回执未记录**：从既有成功 sft job/result/candidate_state 继续，不生成第二个训练目录重训。
- **持续 provider/transport 失败**：记录 distinct failed turns/error events（代码 30s 重试，无自动有限次数）；达观测阈值后按当前 attempt identity 停止、reconcile、必要 cleanup，明确持续看护 owner；健康恢复后同 thread 新 supervision 恢复。

恢复演练（无 API/GPU）至少覆盖 B1 accept→B2 retain 跨轮边界，以及成功 SFT result 已存在/训练未封口两路径（见 `test_five_round_lifecycle.py` 与汇总器恢复测试）。4h 不自动续开新四小时窗口。

## 8. occupier 恢复（仅在 cleanup completed 后）

只有本 attempt **真实资源 cleanup receipt completed** 且 GPU/owner 再检查通过后恢复 occupier；恢复另写 observation，关联 cleanup hash，记录新 PID/PGID/start_ticks/精确 argv，原子更新 identity，再验证四 rank/GPU 负载。资源 unknown/job pending → receipt 保持 pending，occupier 保持未恢复并明确交接持续看护 owner，绝不重复启动。occupier 恢复后 GPU 被授权占用不使历史空卡 cleanup 无效，但**绝不拿 occupier 恢复后的读数填“无 compute apps”**。

已实现入口 `reconcile_deepcoder_run.py` 的 `restore_occupier` + 真实 start helper `_start_occupier_process`：解析本 attempt 最新 cleanup receipt，`status!=completed`/binding 不符/缺完整 owner/registry/GPU/ports inventory/GPU 非空 → 抛错（CLI 非 0 退出，`set -e` 阻断），**绝不启动 occupier**；completed 且空卡再确认后经同一全局 GPU lifecycle flock 调用真实 start helper（默认 `/data/wuxuecheng/z-occupier-ready/run.sh`，GPU0-3，NPROC=4），处理 exec/self-daemon 时序，采集唯一真实 PID/PGID/start_ticks/精确 argv，原子更新共享 `runtime/dev4/identity.json`，核验四 rank GPU 负载，失败按保存的 PID/PGID/start_ticks 逐成员有界调和，写 `rsiflow_occupier_restore` observation 关联 cleanup hash。handoff/start/cleanup/restore 全程共用全局锁 `/tmp/rsiflow-gpu-lifecycle.lock`（`flock` 排他，锁冲突拒绝）。**不得直接 nohup 绕过 helper/锁。**

```bash
# cleanup completed 且空卡再确认后，经 helper + 全局锁恢复（CLI 接线到真实 start helper）：
"$PYTHON" "$ROOT/reproduction/scripts/reconcile_deepcoder_run.py" restore \
  --config "$CONFIG" --run-dir "$RUN_DIR" --supervision 0001
# helper 内部：Popen run.sh(start_new_session, CUDA_VISIBLE_DEVICES=0,1,2,3 NPROC=4) →
# 有界等待 exec/self-daemon  settle 为四 rank 组 → 复核 PID=PGID/start_ticks/argv →
# 原子写共享 identity.json → 实测四卡负载 → 写关联 cleanup 的 restore receipt；失败逐 identity 有界调和。
```

### 8.1 真实 torchrun 拓扑与 NVML PID namespace（real-layout fix）

真实 `run.sh` 是 `exec torchrun`，四个 `train.py` rank 是**独立 session/PGID**（各自 PID=PGID），**PPID 均为 leader**，并非同 PGID 五成员。helper 按 **PPID==leader + argv 匹配 train.py** 发现 rank，逐个记录各自 PID/PGID/start_ticks/argv/PPID，并只读必要 `LOCAL_RANK/RANK`（不读其他 env）做逻辑映射，要求 LOCAL_RANK 恰为 {0,1,2,3}。

NVML `--query-compute-apps` 的 compute PID 与容器内 rank PID **可能属不同 PID namespace**（现场曾见 NVML 3842480… vs 容器 24920…，process_name Not Found）。**不得伪造 PID 相等**：helper 以**四唯一 GPU UUID + 真实负载（used/util>0）+ 四 rank/leader 拓扑**共同验收，记录 `pid_mapping=direct|namespace_differs` 与 `namespace_notes`，保留限制。GPU compute apps 查询字段为 **`pid,gpu_uuid`**（`uuid` 非法字段会 exit 2）；主 `--query-gpu` 的 `uuid` 字段本来有效，不改。

停止/失败调和按**每个保存 identity 分别核验**：leader TERM 后有界观察 rank 自然退出，仅对仍存活且身份匹配的 rank 逐个 TERM/KILL；**leader 消失不等于 rank 退出**，不宽泛 kill、不按名字/端口杀。离线测试用受控 leader+四独立 PGID rank（带 LOCAL_RANK）、不同 NVML PID 正例与错误拓扑（缺 LOCAL_RANK）反例、 bystander 不被误杀正例覆盖。

## 9. 完成判定与报告

最终完成同时核验 SOP §9 各项（state complete/last_turn completed、A0+B1–B5 milestones、current_stage COMPLETE/finish_allowed、B5 前无提前 finish、每轮单 attempt、active 链、durable jobs 终态、supervision exit/reconcile、launcher/Codex PID 不存活、owned registry services_stopped、GPU 无计算进程）+ external cleanup completed。任何缺失只报告实得轮次与 blocker。报告逐轮表格、跨轮 active/skills 链、MODEL/SFT 实际发生轮次、过程异常限制；五轮工程完成不等于五域/3000/694/744/作者复现。