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

1. 实时读取 occupier `pid/pgid/start_ticks/owner/gpus`（owner=dev4-rsiflow-idle-occupier，GPU 0–3），核验 leader 精确 argv、PID=PGID 和 start identity；在 leader 仍活时按 PPID+torchrun/train.py argv发现四个独立 PGID ranks，逐个记录 PID/PGID/start_ticks/PPID/argv/LOCAL_RANK/RANK 并两次快照封存。发现不全/不可读 → pending，不停止 occupier、不发信号。
2. handoff 使用 stage1 封存 topology；先核验所有保存的 leader/rank identity 与父子归属，再 TERM leader并有界观察全部五个 identity。仅对仍存活、PID/PGID/start_ticks 仍逐项匹配的原 rank 逐个 TERM/KILL；leader消失不代表rank退出。缺 topology、reparent 前未封存、身份变化/读取unknown → pending，禁止leader-only降级、宽泛kill、pgrep/pkill或按端口信号。
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

写 stop-requested → identity-checked TERM → 有界等待 exit+complete reconcile（手动停止语义 143；timeout 124/137）。**每次重发 PGID TERM 前都重新核验 `/proc` 的 PID=PGID、start_ticks 与 run/attempt argv**（`_signal_owned`）：进程 gone/zombie → 不再发信号、只等既有 exit/reconcile；identity 变化/PID reuse/读取不确定 → fail closed 抛错并停止发信号，绝不向复用的数字 PID/PGID 发 TERM。不 pkill/按端口。durable jobs/services/app-server 另 session，不在此停止。

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
5. 原子封口：每次新 exclusive observation 目录（不覆盖历史 pending/failed/completed），临时文件+fsync+原子 rename；全成功才 completed，服务停止失败=failed，job 活/identity unknown=pending。**`not_created_verified` 路径**（在任何 job/service 创建前失败）：仅当 registry 从未创建（before/after hash 均 null、文件不存在）、无 job/training phase/inference service owner、launcher/app-server 已退出、两次 GPU 实测均无 compute apps 且 free_verified、端口 closed、可信 launch chain 齐全、且 `finish_allowed=False`（非正常完成）时才 completed，registry.phase 记 `not_created_verified`（不伪造 services_stopped）。**历史证明（producer/consumer 共用 `not_created_history_proof` helper）**：要求可信早期协议状态 `current_stage==A0` 且 A0.remaining 仍含 bootstrap（未完成）；连续、完整、可解析的有序 turn command/receipt（数字 turn 0000..N 无缺口、每 turn command+receipt 均可解析）；有序 turn 历史中无 compute/service 创建调用（含 `run_command`，因其为 LONG_OPERATION 会创建 durable job）；`workflow_events.jsonl` 非空、逐行可解析、与 turns 有序对应（每事件引用现存 turn、按 turn 非递减、覆盖全部 turn、operation 与 command 一致）。任何缺口/损坏/空 events/不一致 → pending。producer 将证明（含 `history_sha256`）封入 receipt.not_created_evidence；**consumer 不信摘要，从当前历史重算同一 helper 并比对 history_sha256**，摘要与当前历史不符 → 拒绝；restore 锁内继承该重算。五轮正常完成（finish_allowed=True）或存在 jobs/phases/services 而缺 registry → 仍阻断，不降格 not_created/stopped。pending/failed 绝不恢复 occupier。
6. receipt 字段/绑定/hash 按 Codex 方案（schema_version/kind/status/blockers/writer/binding/supervision/protocol/owners/registry/gpu_observations/ports/actions/errors/evidence_files_sha256/occupier）。消费者逻辑：`run_complete = protocol_complete AND launcher_exited AND external_cleanup.completed AND 全部必需终态证据有效`；protocol complete 但 jobs/services pending → “协议完成，外部资源待调和”，不重启 complete run。

**resume launcher pre-state missing-key 窄路径（`prestate_launch_evidence`）**：只适用于已有真实协议状态的 resume 在 `PersistentMeta`/新 app-server 创建前因缺 provider key 退出，不是通用 launch-chain 绕过，也不是 §6.3 的空目录拒绝或早期 A0 `not_created_verified`。producer/consumer 共用 `prestate_launch_cleanup_proof`，并独立重算、逐字段比较：
- 当前 attempt 必须有绑 run/config/attempt 的 prelaunch seal、launch seal、真实 supervisor identity、identity-pending、exit=1、complete/empty reconcile；`launch.binding.mode=resume`，`resumed_from` 必须是紧邻的前一个 real attempt。`post-launch-identity.json` 或 `launch-final.json` 任一存在即拒绝。
- `launcher.log` 必须是完整 canonical traceback：首行/末行精确、仅 `launch_meta.py` 的 `<module>` → `main` → `_autodl_key` 三帧，frame path/line/source 与当前源码逐行匹配，只允许 Python `^`/`~` 定位行；末尾是精确 `OPENAI_API_KEY is unset and no compatible provider key was found`。同时从 AST 重证 `_autodl_key` 在 `PersistentMeta` 前。前后附加错误、意外帧、空/多/歧义 traceback、host/backend/thread/config/run/attempt 不符均拒绝。
- prelaunch 的 resume receipt path+hash 必须链到 `resumed_from` 的 completed cleanup。独立核 previous cleanup kind/status/binding，并用截至 previous attempt 的**显式历史 supervision 前缀**重新走 cleanup consumer；嵌套 pre-state consumer 也必须把该前缀逐层传入，不能拿全局最新 attempt。当前 `state.json` hash 必须逐字节等于 previous `protocol.state_hash`，finish 必须非 complete。
- workflow stage 默认必须与 previous 相同。唯一兼容例外是 reviewed commit `beebd9b42b734c0f5a1dd6ba927931ee76b81f43` 的 A0→B2 重建：必须用当前 turns 和生产 recognizers 独立重算，A0/B1 complete、当前 B2.parent_rollout、仅 B2–B5 incomplete，且 workflow observed_turns 等于当前完整 inventory；其他 stage 变化或无效/缺证据历史均拒绝。
- 当前 jobs/training-phase source path/hash 必须与 previous cleanup owner/source snapshots 完全相同且 terminal/exited；每个 job 独立拥有的 `controller_transport/<job>/result.json` 与 `request.json` 也必须逐项 hash 等于 previous snapshots，async running turn receipt 不允许 terminal result 独立漂移。registry 必须仍为 `services_stopped`，当前 hash 等于 previous `registry.after_hash`。
- 默认 previous cleanup 必须含完整 `protocol.turn_inventory`（连续 turn、每项 command/receipt SHA256），且每项都与 previous source snapshots 绑定；当前完整 turn hash inventory 必须与其逐项相同。新增/修改 turn（包括 backdated 或保留 mtime）均拒绝；mtime 不是有效性证据。
- **旧格式 legacy 兼容仅限上述已验证 A0→B2 特例**：previous 可缺完整 turn baseline，但其 turn command/receipt snapshots 必须与 previous jobs 的 `turn_dir` 推导集合**精确相等**，不能仅检查数量或连续性。实际 0003 的非连续集合为 `0018,0035,0037,0042,0045,0049,0052,0061,0065`；缺任一 anchor、增加非 job-owned anchor 或内容变化均拒绝。结合 immutable previous cleanup/0004 prelaunch、全部 job/result/request/registry 重绑定、生产重建和 PersistentMeta 前 missing-key，证明的是“0004 launcher 未产生新工作”；**不证明全部 75 个 turn 的历史字节完整性，也不能识别所有 backdated 外部修改**。当前 75-turn hash 只向后续 consumer 提供新基线，不得把此兼容路径表述为 byte-for-byte historical turn immutability。
- launcher owner 只能取 `launch.supervisor` 与 identity.json 一致的真实身份，并重新测量已退出。app-server 只能写 `rsiflow_app_server_not_created` 显式证据，`identity=null/state=not_created`，不得用空 identity 冒充 exited。expected GPUs 只取 hash/binding 已验证的 prelaunch seal；当前 owner/service exit、两次 GPU raw+parsed 空闲测量和 ports bind-close 仍是必需条件，缺 key 不豁免任何实际资源清理。
- 全部满足时 receipt 保持 registry `services_stopped`、`stop_action=prestate_verified`，并封存 `prestate_launch_evidence`；summarizer `_validate_cleanup` 必须从当前文件重算同一证明，不能只信 receipt 状态。`observe-resume-preflight` 继续使用该 consumer；下一次 resume launch gate 也只有在该 consumer 对最新 real prior 重算通过后才可用 supervisor-only chain，其他 attempt 仍要求完整 runtime chain。不得据此把目录标为 preflight-rejected、伪造 runtime identity 或跳过 cleanup 直接开下一 supervision。

### 6.3 prelaunch 拒绝留下的空 attempt 调和（`preflight_rejected`）

preflight seal/资产验证拒绝**可能在 supervision 下留下空 attempt 目录**（无 identity/launch/exit/reconcile/进程/产品）。这种空目录会被误判为 current attempt，使上一轮 completed cleanup 在 resume preflight 被判 `not_current_attempt`。合同：

- **未来预防**：real-mode launch 的 prelaunch seal+资产验证在**创建 attempt 目录之前**完成；`verify_prelaunch_seal` 仅在全部门通过后由其末尾 `write_once` 原子创建目录，拒绝不留空目录（`_validate_launch_gates` 已先拒绝任何已存在目录）。
- **现有空目录调和**：不得由 agent 直接编 JSON、不得删除目录、不得伪造 identity。用经审阅的生产命令从**实际目录/进程/产品证据**生成不可变标记：

```bash
"$PYTHON" "$ROOT/reproduction/scripts/reconcile_deepcoder_run.py" mark-preflight-rejected \
  --config "$CONFIG" --run-dir "$RUN_DIR" --supervision 0002
# 拒绝（存在 identity/launch/exit/reconcile/seal/产品、活进程引用、或 job/training 产品绑定本 attempt）→ 非 0 退出，set -e 阻断。
"$PYTHON" "$ROOT/reproduction/scripts/reconcile_deepcoder_run.py" inspect \
  --config "$CONFIG" --run-dir "$RUN_DIR" --supervision 0001   # 读 rejected_attempts 与 next_supervision
```

- **共享分类**（producer/consumer 同一 `supervision_attempts`/`preflight_rejected_proof`）：仅当目录有绑定 run/attempt/config/host/observed_at 的 `rsiflow_preflight_rejected` 标记**且真正空目录（内容级：除 marker 本身外无任何 entry）**时才归为 rejected；否则一律视为真实 attempt（即便未封口）。**任何已知/未知/嵌套 entry（identity、launch、external-cleanup/obs-*、owner/product 证据等）或活进程都不得按空拒绝处理**（非文件名 allowlist），拒绝项绝不隐藏真实 failed owner。producer 在 **lifecycle 锁内**重新核目录空/产品/活进程后才写不可变 marker；consumer 独立重算同一证明。
- supervision/cleanup 消费者把 rejected 排除出 real attempts（current-attempt/accounting/inventory 只对 real），但**独立重算每个 rejected 的证明**；历史 receipt 的 rejected 列表须为当前 genuine rejected 的**子集**（封口后新增的拒绝允许；receipt 列为拒绝但已变真实 attempt 则拒绝）。因此**历史 0001 completed cleanup 仍可用于 resume preflight**。
- **resume launch gate**（`_validate_launch_gates`）同样用共享 real prior 列表并独立核 rejected proof：accounting/resumed_from/launch chain/thread seal/owner 连续性全部绑定**最新 real prior**（如 0001），rejected 0002 不阻断；真实 failed 0002（含 identity/未封口 reconcile）仍拒绝。
- **编号**：rejected 目录仍占用其编号、绝不复用；`next_supervision_number` = 全部数字目录（含 rejected）max+1（如 0002 拒绝 → 下一个 0003）。新 resume preflight（§7.1）验证标记后以 inspect 取得的 next 编号启动（不硬编码）。

## 7. 恢复（同 thread，已完成 SFT 不重做）

恢复前封存旧 thread_id、active Task/hash、policy/config/backend、completed milestones、job/result/request、训练 marker。新 supervisor 用相同 config/run/code/backend，记录 `resumed_from`，启动后验证 thread_id 与旧值精确相同、只有新 supervision 编号变化。不删除 state/codex_home/policy/lock/history，不重放 seed，不复制旧单轮 thread。

分支合同：
- **state=complete**：只做外部 cleanup（§6.2），不 resume。
- **incomplete/disconnected**：允许恢复；从下一 milestone 继续，不重复 A0/父代 rollout/已验证候选/SFT。
- **早期 A0 且 active_task.json 从不存在**（bootstrap 在 A0 封口前失败）：仅当共用 `not_created_history_proof` 仍证明 `current_stage==A0`、bootstrap 未完成、有序历史无 compute/service 创建（含 run_command），且磁盘无 jobs/training phase/候选/训练产品（candidate_state/checkpoint_trained/training_metrics/gpu_phase_receipt）与 service registry 时才允许 resume（`_early_a0_no_active_proof`）；active Task 由**生产 bootstrap 创建一次**，supervisor 绝不伪造 active state。空 job 库存哨兵 `<no job inventory>` 对该已证明路径视为预期（真实 invalid/missing 绑定仍阻断）。**任何 B1+ 阶段、历史缺口/损坏/不一致、或已有产品但 active 缺失 → `requires_audit`**。其余 resume 门（thread/config/backend/owners/launch chain/thread seal）一律不跳过。
- **active job 仍运行**：等待自然终态或既有 owner `job_status`/`wait_job`+start identity 调和；未调和前禁 cleanup completed/新 resume。
- **dead job 缺 result 或 SFT receipt 非终态**：`requires_audit`，不因新 supervision 编号再调用 sft。
- **metrics/checkpoint 已完成但候选/技能回执未记录**：从既有成功 sft job/result/candidate_state 继续，不生成第二个训练目录重训。
- **持续 provider/transport 失败**：记录 distinct failed turns/error events（代码 30s 重试，无自动有限次数）；达观测阈值后按当前 attempt identity 停止、reconcile、必要 cleanup，明确持续看护 owner；健康恢复后同 thread 新 supervision 恢复。

恢复演练（无 API/GPU）至少覆盖 B1 accept→B2 retain 跨轮边界，以及成功 SFT result 已存在/训练未封口两路径（见 `test_five_round_lifecycle.py` 与汇总器恢复测试）。4h 不自动续开新四小时窗口。

### 7.1 resume 专用当前 preflight（替代过期 stage1/2，无需 occupier handoff）

资源已 cleanup completed（occupier restore 可能仍 pending）时，**不得为新鲜 resume 重跑不安全的 occupier 恢复，也不得复用过期 stage1/2 provider 证据**。resume 必须采一份**当前** preflight receipt：

```bash
# dev4 bash；元组按 §0 重设。先有本 attempt 的 completed cleanup receipt（§6.2）。
set -euo pipefail
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
AUDIT_DIR="$ROOT/reproduction/logs/five-round-resume-preflight-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$AUDIT_DIR"
# 当前 provider 200/completed/gpt-6-astra 探针（凭据安全注入，不记录 key）
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" probe-provider \
  --config "$CONFIG" --output "$AUDIT_DIR/provider-probe.json"
# 当前 resume preflight：绑当前 provider/host/boot/ns + 空闲 GPU/ports 实测 + 已有 completed cleanup receipt
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" observe-resume-preflight \
  --config "$CONFIG" --provider-report "$AUDIT_DIR/provider-probe.json" \
  --cleanup-receipt "$CLEANUP_RECEIPT" --output "$AUDIT_DIR/resume-preflight.json"
# prelaunch input 用 --resume-preflight 替代 --stage1/--stage2（fresh 仍必须原两阶段）
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" preflight-input \
  --config "$CONFIG" --resume-preflight "$AUDIT_DIR/resume-preflight.json" \
  --output "$AUDIT_DIR/prelaunch-input.json"
# 编号不硬编码：从 inspect 取 next_supervision（含 rejected 目录，防复用）与最新 real prior。
# 例：0001 real completed、0002 已 mark-preflight-rejected → NEXT=0003、RESUMED_FROM=0001。
NEXT=$("$PYTHON" "$ROOT/reproduction/scripts/reconcile_deepcoder_run.py" inspect \
  --config "$CONFIG" --run-dir "$RUN_DIR" --supervision 0001 \
  | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["supervision"]["next_supervision"])')
RESUMED_FROM=$("$PYTHON" "$ROOT/reproduction/scripts/reconcile_deepcoder_run.py" inspect \
  --config "$CONFIG" --run-dir "$RUN_DIR" --supervision 0001 \
  | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["supervision"]["attempts"][-1])')
# resume launch（同 thread/config/backend；continuity 绑定最新 real prior，其余 resume 门全保留）
"$PYTHON" "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" launch \
  --config "$CONFIG" --supervision "$NEXT" --launch-mode resume --resumed-from "$RESUMED_FROM" \
  --timeout 4h --kill-after 5m --preflight-seal "$AUDIT_DIR/prelaunch-input.json"
```

合同（`observe-resume-preflight`/`_validate_resume_preflight`/`verify_prelaunch_seal(mode="resume")`）：
- resume receipt 绑**当前** provider 200/completed/model（探针 `observed_at` 须在 1800s 内）、host/boot_id/pid_namespace、空闲 GPU（无 compute apps、free_verified）与 closed ports 实测、当前 config_hash/run_dir。
- 绑已有 **completed** external cleanup receipt（kind/status/binding run+config 匹配）；**cleanup consumer 必须对当前 run/history/owners 重算通过**（不信摘要），且 receipt 所绑 launcher/app-server/jobs/services/training 当前 owner **逐项 live 复核仍 exited**。
- **无需 occupier running/handoff**（资源已清理）；不重跑 occupier restore，不改时间戳/不伪造观测。
- `verify_prelaunch_seal` 保留全部资产/reviewed HEAD/backend 门；**fresh 模式拒绝 resume receipt**（必须原两阶段），resume+stage 混用拒绝；resume 无 resume receipt 时仍须满足两阶段门（无通用绕过）。seal binding 记 `launch_mode`。
- 过期/未完成 cleanup、当前 owner 仍活、缺/过期 provider 一律拒绝（实验命令不执行）。早期 A0 无 active 的 resume 另受 §7 `_early_a0_no_active_proof` 约束。

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

真实 `run.sh` 是 `exec torchrun`，四个 `train.py` rank 是**独立 session/PGID**（各自 PID=PGID），**PPID 均为 leader**，并非同 PGID 五成员。helper 按 **PPID==leader + argv 匹配 train.py** 发现 rank，逐个记录各自 PID/PGID/start_ticks/argv/PPID，并只提取并记录必要 `LOCAL_RANK/RANK`，不记录其他 environ 字段做逻辑映射，要求 LOCAL_RANK 恰为 {0,1,2,3}。

NVML `--query-compute-apps` 的 compute PID 与容器内 rank PID **可能属不同 PID namespace**（历史只读诊断曾见 NVML 3842480… vs 容器 24920…，process_name Not Found）。**不得伪造 PID 相等或由PID不等推断 namespace 不同**：只有 `/proc/<NVML PID>/status` 的 NSpid 与容器rank链、PID namespace inode、start_ticks共同验证时才可标 `nspid_verified`；否则 `unresolved/pending`，保留两边原始证据，不通过handoff/restore。四唯一 GPU UUID、真实负载及四rank/leader拓扑是必要条件，不替代PID归属证据。GPU compute apps 查询字段为 **`pid,gpu_uuid`**（`uuid` 非法字段会 exit 2）；主 `--query-gpu` 的 `uuid` 字段本来有效，不改。

停止/失败调和按**每个保存 identity 分别核验**：leader TERM 后有界观察 rank 自然退出，仅对仍存活且身份匹配的 rank 逐个 TERM/KILL；**leader 消失不等于 rank 退出**，不宽泛 kill、不按名字/端口杀。离线测试用受控 leader+四独立 PGID rank（带 LOCAL_RANK）、不同 NVML PID 正例与错误拓扑（缺 LOCAL_RANK）反例、 bystander 不被误杀正例覆盖。

## 9. 完成判定与报告

最终完成同时核验 SOP §9 各项（state complete/last_turn completed、A0+B1–B5 milestones、current_stage COMPLETE/finish_allowed、B5 前无提前 finish、每轮单 attempt、active 链、durable jobs 终态、supervision exit/reconcile、launcher/Codex PID 不存活、owned registry services_stopped、GPU 无计算进程）+ external cleanup completed。任何缺失只报告实得轮次与 blocker。报告逐轮表格、跨轮 active/skills 链、MODEL/SFT 实际发生轮次、过程异常限制；五轮工程完成不等于五域/3000/694/744/作者复现。

**2026-11-19 实际终局**：该固定 16 条单域五轮已达到上述完成判定，`run_complete=true`、最终 supervision `0008` exit 0、external cleanup completed，四卡在 cleanup 时空闲。逐轮结果与 attempts/recovery 历史见 `deepcoder-five-round-real-results.md`。Occupier 自动恢复因 NVML PID ownership 未解决而 stopped/`restore_pending`，没有强行通过自动恢复门；后续不得再从 COMPLETE run 重放实验。最终汇总器会检查完整 activation inventory：retain 拒绝任何成功 activation，accept 只接受唯一且正确绑定的成功 activation；selection 的 `accept_or_retain` 优先于 `selection`，与生产 progress 一致。

**2026-11-19 cleanup 后用户授权的直接启动**：用户明确要求可在占卡目录用 `tmux` 直接 `bash run.sh`。主 agent 先实测四卡均无 compute app、每卡仅 1 MiB，再启动独立会话 `rsiflow-idle-occupier`，参数为 `--mem-mb 2500 --log-interval 20`，日志写 `runtime/dev4/tmux-occupier-20261119.log`。实测四 rank 持续训练、每卡脚本显存约 2519 MB；这不属于上述自动 restore receipt，也不改写 cleanup 时的空卡观测。后续新实验必须先核对并停止该自有 tmux/torchrun 进程组，确认四卡满足空闲门，不能与之抢卡。
