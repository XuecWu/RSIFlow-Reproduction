# DeepCoder function 16 canary：dev4 执行 runbook

> 状态：**真实 dev4 canary 已完成。** 当前 workspace run 的 supervision 0002 同 thread 恢复后 exit 0、reconcile complete，HARNESS accept/activate、state COMPLETE、9 个 durable jobs 最终回执和四卡 cleanup 均已完成；结果为 3/16→4/16，质量限制见 `deepcoder-canary-real-results.md`。
> 当前 `state.json.status=complete` 的 run 不得重新启动或覆盖。任何新的真实执行都需要单独 GO；任何红测试、未关闭的 Critical/Important、目标 HEAD 不一致、凭据缺失或 bwrap 校验失败都阻止启动。

## 1. 范围与证据边界

- 机器配置：`reproduction/configs/train_4b_deepcoder_canary_workspace.json`。
- 当前 output/run 由该配置自然派生：`output_root=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/Rollout_logs/deepcoder_canary_16_v1`，`run_name=deepcoder_function_16_canary_workspace_v1`；下文 monitor、stop、cleanup 和 resume 均使用这一对路径。
- 旧 `train_4b_deepcoder_canary.json` 指向 preflight native workspace 外的 output/run，真实启动已被 host-side 写门正确拒绝；该 blocked 历史保留，但不再是任何操作命令的默认目标。
- 冻结输入：`reproduction/logs/deepcoder-canary-16-v1/release/`，固定为父 B1 原顺序前 16 条 `deepcoder_taco` function 记录，不是看结果重选。
- 一轮 A0+B1；A0 不做 baseline validation，B1 只有一个 attempt，候选仍在相同 16 条任务上配对比较。
- `experiment_scope=deepcoder_canary`，有效 domain 只有 `code`，runtime quotas 精确为 `tool_use/code/searchqa = 0/16/0`。这不是放宽其他 scope 的零配额规则。
- `evaluation_schedule=none`，`retry_rejected_from_round=2`。第 1 轮 `retain_parent` 是 terminal：完成 comparison、skills、context、snapshot 后以 `completed_retained` 结束，不激活、不重选、不开启另一 attempt/round。
- 这不是五轮、3000 任务、SFT 实验或最终 694/744；没有独立 validation/benchmark，不能产生模型质量或最终评分结论。
- Meta 实际选择且只能选择 `HARNESS`、`MODEL`、`ARTIFACTS` 之一。报告必须读取 `decision.json`、selection、Task state 和组件回执，写明实际组件；不得预先假定 MODEL，也不得把 HARNESS/ARTIFACTS 改记为 MODEL/SFT。
- 只有 Meta 实际选择 `MODEL`，且当前父代训练 rollout 中存在经评估验证的自然成功轨迹时，才允许 positive-only LoRA SFT。没有自然正例则 SFT 为 blocked/not run，不换题、不造标签、不借 held-out；任何未实际执行的训练都明确标为“未运行”。

离线审查入口：Tasks 1–3 报告及其 scoped reviews 位于 `.superpowers/sdd/2026-11-17-deepcoder-canary/`。Task 4 不重复 broad review；最终 whole-branch review 属于 Task 6。

## 2. 访问与本地 SSH 设置

动态 TCP 入口、用户和既有私钥路径只从 `reproduction/audit/reviews/dev-four-a800-access.md` 获取。该入口可能过期；不要从旧命令、AGENTS 或本 runbook 猜测，不要把私钥内容、cpolar authtoken、密码或 Meta token 复制到文档、命令行或日志。

在本机 shell 中按上述 access report 的**当前值**设置以下占位符，然后只做认证和只读核验：

```bash
DEV4_HOST='<access report 当前 host>'
DEV4_PORT='<access report 当前 port>'
DEV4_IDENTITY='<access report 所列既有私钥路径>'
DEV4_SSH=(ssh -i "$DEV4_IDENTITY" -p "$DEV4_PORT" \
  -o BatchMode=yes -o IdentitiesOnly=yes "root@$DEV4_HOST")
"${DEV4_SSH[@]}" 'hostname; test -d /data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight'
```

若动态入口失效，停止并向 controller 请求新入口；不切换到来历不明的主机或凭据。

## 3. dev4 启动前门

以下命令在 **dev4 的受控 shell** 中执行。`REVIEWED_HEAD` 由 controller 在 GO 时提供；本文件不写死 HEAD、PID 或动态 TCP。`set -euo pipefail` 使任一检查失败立即退出，不能继续到 `--check` 或启动：

```bash
set -euo pipefail
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
CONFIG="$ROOT/reproduction/configs/train_4b_deepcoder_canary_workspace.json"
OUTPUT_ROOT="$ROOT/Rollout_logs/deepcoder_canary_16_v1"
RUN_DIR="$OUTPUT_ROOT/runs/deepcoder_function_16_canary_workspace_v1"
LAUNCH_LOG="$RUN_DIR/meta_session/launcher.log"
REVIEWED_HEAD='<GO 时批准的完整 reviewed commit>'

test -n "$ROOT" && test -d "$ROOT"
test -n "$PYTHON" && test -x "$PYTHON"
test -n "$CONFIG" && test -f "$CONFIG"
test -n "$OUTPUT_ROOT" && test -n "$RUN_DIR"
test -n "${REVIEWED_HEAD:-}"
cd "$ROOT"
test "$(git rev-parse HEAD)" = "$REVIEWED_HEAD"
git diff --exit-code
git diff --cached --exit-code
test -x "$ROOT/assets/isolation/bin/bwrap"
test "$(sha256sum "$ROOT/assets/isolation/bin/bwrap" | cut -d' ' -f1)" = \
  d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca
test -f "$ROOT/assets/isolation/install-receipt.json"
test -f "$ROOT/reproduction/logs/deepcoder-canary-16-v1/READY.json"
test -f "$ROOT/reproduction/logs/deepcoder-canary-16-v1/release/B1/manifest.json"

# 只读 CLI preflight；返回 0 本身不是真实运行 PASS。
"$PYTHON" "$ROOT/RSIFlow_4B/launch.py" --config "$CONFIG" --check
```

确认 `--check` 输出中的 config/run dir 与上述变量一致，rounds=1、每轮 16、空 skill seed、所需资产存在。它不会验证 GPU、API、真实 sandbox 执行、组件选择或 694 协议。

### 既有 Meta 凭据的安全注入

真实启动前，由获授权操作员通过 dev4 既有安全机制把 Meta key 注入**当前远端进程环境**；本 runbook 不读取、生成、打印或写入凭据，也不使用 `set -x`、`env`、`printenv` 或命令行赋值传递 key。有效 base 配置要求变量名为 `OPENAI_API_KEY`。

在同一个远端 shell 中只检查存在性；缺失时停止，不回退到仓库文件或另一 provider：

```bash
if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo 'OPENAI_API_KEY is not present in the injected environment; stop.' >&2
  exit 1
fi
export OPENAI_API_KEY
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export ROOT PYTHON CONFIG OUTPUT_ROOT RUN_DIR LAUNCH_LOG
```

不得省略或更改 `RSIFLOW_SANDBOX_BACKEND`。bwrap 失败是 infrastructure error，不得回退 host、Landlock 或 `danger-full-access`，也不得记成模型零分。

## 4. 新运行：唯一已验证的 detached supervisor

新运行要求 run dir 不存在；若已存在，停止并转到恢复章节，不能覆盖历史 attempts。每次启动或 resume 都使用新的 supervision attempt；本次新运行为 `0001`，以后不得复用该目录或读取旧 attempt 的 exit 作为本次状态：

```bash
if [ -e "$RUN_DIR" ]; then
  echo 'Run dir exists; use the resume procedure.' >&2
  exit 1
fi
ATTEMPT=0001
ATTEMPT_DIR="$RUN_DIR/meta_session/supervision/$ATTEMPT"
mkdir -p "$ATTEMPT_DIR"
export ATTEMPT ATTEMPT_DIR
```

**4 小时只监督 wrapper 内 launcher 前台进程及其同一 PGID 的 wrapper/timeout。** 这里的“前台进程”是未使用 CLI `--detach` 的受监督 launcher，不是操作员 shell 启动模式。它不自动停止另建 session 的 durable jobs、GPU services 或 bwrap Meta app-server，不是整个 canary 的进程/GPU 硬边界。到期后必须按第 6 节逐项调和，不能直接宣称 cleanup completed 或 resume。

真实 canary **不提供 foreground 启动备选**：不要在操作员 shell/tee 所在 PGID 上另写调和器，也不要恢复旧的 `timeout --foreground … | tee` 命令。唯一入口是下面已通过两层嵌套自动 timeout、手动停止和 cleanup 拒绝探针的 detached supervisor。不要添加 `--prepare-data`；输入已冻结。124 表示 launcher 监督 timeout，137 常见于 kill-after 升级；它们不是整个实验已停止的证据。

### 4.1 唯一入口：远端 detached，本机 monitor

当前 CLI 虽有 `--detach`，但它创建的 child 会脱离启动命令。**禁止使用 `timeout 4h … --detach`**。以下方式不传给 launcher `--detach`，而用 `nohup setsid` 创建独立 session，并用 `timeout --foreground` 保持 wrapper、timeout、直接 launcher 及其实际控制子进程在同一个已记录 PGID。**不假设 `--foreground` 递归杀子进程**：timeout 返回后，wrapper 先对同 PGID 做有界 TERM，再仅对重新核验 PID/PGID/start identity 的剩余成员逐个 KILL；调和器自身忽略 TERM，wrapper 保留 trap，因而能写 `reconcile.json` 和原始 launcher exit。独立 session durable jobs/services/app-server 不属于该 PGID，不在扫描或信号范围内：

```bash
printf '%s\n' detached > "$ATTEMPT_DIR/kind"
nohup setsid bash -c '
  ROOT=$1; PYTHON=$2; CONFIG=$3; RUN_DIR=$4; ATTEMPT_DIR=$5
  trap ":" TERM
  set +e
  timeout --foreground --signal=TERM --kill-after=5m 4h \
    "$PYTHON" -u "$ROOT/RSIFlow_4B/launch.py" \
    --config "$CONFIG" --run-dir "$RUN_DIR"
  rc=$?

  # timeout --foreground 只保证直接命令收到 timeout 信号，不假设递归终止其子进程。
  # timeout 返回后，仅对已经验证的 launcher PGID 做有界 TERM/KILL 调和。
  "$PYTHON" - "$ATTEMPT_DIR" "$$" 30 <<'PY'
import json
import os
from pathlib import Path
import signal
import sys
import time

attempt_dir = Path(sys.argv[1])
wrapper_pid = int(sys.argv[2])
grace = float(sys.argv[3])
helper_pid = os.getpid()
signal.signal(signal.SIGTERM, signal.SIG_IGN)

def members():
    result = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            pid = int(entry.name)
            if (int(stat[2]) == wrapper_pid and stat[0] != "Z"
                    and pid not in {wrapper_pid, helper_pid}):
                result.append({"pid": pid, "start_ticks": stat[19]})
        except (FileNotFoundError, PermissionError):
            pass
    return result

initial = members()
if initial:
    os.killpg(wrapper_pid, signal.SIGTERM)
deadline = time.monotonic() + grace
while time.monotonic() < deadline and members():
    time.sleep(0.05)
remaining = members()
killed = []
for item in remaining:
    try:
        stat = Path(f"/proc/{item['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        if (int(stat[2]) == wrapper_pid and stat[19] == item["start_ticks"]
                and stat[0] != "Z"):
            os.kill(item["pid"], signal.SIGKILL)
            killed.append(item["pid"])
    except (FileNotFoundError, ProcessLookupError):
        pass
deadline = time.monotonic() + grace
while time.monotonic() < deadline and members():
    time.sleep(0.05)
remaining = members()
report = {"status": "complete" if not remaining else "failed",
          "term_targets": [item["pid"] for item in initial],
          "kill_targets": killed, "remaining": remaining}
temporary = attempt_dir / "reconcile.json.tmp"
temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, attempt_dir / "reconcile.json")
raise SystemExit(bool(remaining))
PY
  reconcile_rc=$?
  if [ "$reconcile_rc" -ne 0 ]; then
    echo "Launcher PGID reconciliation failed; cleanup and resume remain blocked."
  fi
  printf "%s\n" "$rc" > "$ATTEMPT_DIR/exit.tmp"
  mv "$ATTEMPT_DIR/exit.tmp" "$ATTEMPT_DIR/exit"
  exit "$rc"
' bash "$ROOT" "$PYTHON" "$CONFIG" "$RUN_DIR" "$ATTEMPT_DIR" \
  >> "$LAUNCH_LOG" 2>&1 < /dev/null &
SUPERVISOR_PID=$!

# 原子记录 PID=PGID、start identity 和本次路径；任何不符都 fail closed。
"$PYTHON" - "$SUPERVISOR_PID" "$RUN_DIR" "$ATTEMPT_DIR" <<'PY'
import json
import os
from pathlib import Path
import sys

pid = int(sys.argv[1])
run_dir = Path(sys.argv[2]).resolve()
attempt_dir = Path(sys.argv[3]).resolve()
stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
pgid = int(stat[2])
if pgid != pid:
    raise RuntimeError(f"supervisor PID/PGID mismatch: pid={pid}, pgid={pgid}")
cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
for expected in (run_dir, attempt_dir):
    if str(expected).encode() not in cmdline:
        raise RuntimeError(f"supervisor command does not contain {expected}")
identity = {"pid": pid, "pgid": pgid, "start_ticks": stat[19],
            "run_dir": str(run_dir), "attempt_dir": str(attempt_dir)}
temporary = attempt_dir / "identity.json.tmp"
temporary.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, attempt_dir / "identity.json")
PY
echo "Detached launcher supervision attempt $ATTEMPT started; use its own identity/exit files."
```

SSH 断开不应停止该 session；4 小时到期由远端 `timeout` 独立监督 launcher。独立 session 的 jobs/services/app-server 仍按第 6 节处理。

## 5. 本地 monitor 与状态判定

在本机复用第 2 节的 `DEV4_SSH`，并设置相同路径和**当前 attempt**：

```bash
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
OUTPUT_ROOT="$ROOT/Rollout_logs/deepcoder_canary_16_v1"
RUN_DIR="$OUTPUT_ROOT/runs/deepcoder_function_16_canary_workspace_v1"
ATTEMPT=0002
ATTEMPT_DIR="$RUN_DIR/meta_session/supervision/$ATTEMPT"

# 单次快照
"${DEV4_SSH[@]}" "$PYTHON $ROOT/RSIFlow/monitor_meta.py --run-dir $RUN_DIR --once"
# 持续只读 monitor；Ctrl+C 只退出 monitor，不停止 Meta、rollout 或 GPU 服务。
"${DEV4_SSH[@]}" "$PYTHON $ROOT/RSIFlow/monitor_meta.py --run-dir $RUN_DIR --history 20"
```

重点输出和状态：

- `$RUN_DIR/meta_session/launcher.log`：launcher/Meta 主日志。
- `$RUN_DIR/meta_session/state.json`：`starting/running/disconnected/complete`、thread、launcher/Codex PID 和最近错误。
- `$RUN_DIR/meta_session/workflow_state.json`、`$RUN_DIR/meta_session/RUN_STATUS.md`、`$RUN_DIR/meta_session/workflow_events.jsonl`：阶段、milestone 和 `finish_allowed`。
- `$RUN_DIR/meta_session/app_server/events.jsonl`：monitor 读取的事件流。
- `$RUN_DIR/meta_session/jobs/`：durable job 元数据。
- `$RUN_DIR/controller_transport/`：job request、result、stdout/stderr。
- `$RUN_DIR/controller_receipts/`：控制器事实回执。
- `$RUN_DIR/round_1/`、`$RUN_DIR/snapshots/`、`$RUN_DIR/meta/skills.jsonl`、`$RUN_DIR/meta/context.json`：父子轨迹、decision/selection、候选、经验和快照。
- `$OUTPUT_ROOT/services/four_gpu_services.json`：本 output root 拥有的四卡服务 registry。
- `$ROOT/Meta_logs/deepcoder_function_16_canary_workspace_v1/`：集中 Meta 记录及部分到 Rollout_logs 的链接。
- `$ATTEMPT_DIR/kind`、`identity.json`、`stop-requested`、`reconcile.json`、`exit`：本次 launcher 监督与同 PGID 调和状态。旧 attempt 的文件永不作为本次 exit/reconcile。

`launch_requested`、`already_running`、进程 alive、transport success 或 launcher exit 0 均不等于实验 PASS。完成至少需要 `state.json.status=complete`、`workflow_state.finish_allowed=true`、A0/B1 必需 milestones 齐全，并逐项复核真实回执。若 retain，必须有 terminal-retain 证据且没有后续 attempt/activation/validation；若 accept，必须有 Meta 显式选择和严格正增益的完整同批比较。模型失败、候选执行失败与 infrastructure error 分开报告。

最终报告还要回答：实际组件是什么、16 条父子 ID 是否精确一致、哪些操作 completed/blocked/not run、是否发生自然正例和真实 SFT、是否 retain/accept、是否完成 cleanup。未发生的 MODEL/SFT、五轮、694/744 和 benchmark 必须明确写“未运行”，不能省略成暗示成功。

## 6. 结束核验、手动停止与 cleanup

只读 monitor 的 Ctrl+C 不是停止实验的方法。launcher timeout/stop 与 durable work 的结束是不同事件；没有安全、已审阅的 durable-job 取消命令时，**必须等待自然最终回执**，不得发明 job kill 命令。已有 owner 仍运行时，可用生产 `job_status`/`wait_job` 和 PID start identity 核验调和；未调和前禁止 cleanup completed 或新 resume。

| 结束原因 | launcher / attempt | durable jobs | Meta app-server | owned service registry |
| --- | --- | --- | --- | --- |
| 正常完成 | 当前 attempt 有 exit；detached 还须有 complete reconcile；核对 state=complete 和完整回执，exit 0 单独不算 PASS | 每个 job 必须有自然最终 result/receipt；active/incomplete 则完成结论不成立并继续等待/调和 | 生产 `server.close()` 后核验记录的 Codex PID 已不存在；存活则 cleanup blocked | 核验预期绝对路径；已 stopped/无 registry 才可记对应清理事实，仍有 owned 服务则通过下方门后调用 `stop_services` |
| launcher timeout | 当前 attempt exit 通常为 124/137；wrapper 已做有界 PGID TERM/KILL，仍须核验 complete reconcile 和最终 PGID 为空 | 独立 session 可能继续运行；等待自然最终回执或用既有 identity-checked owner 调和，不能按 124 推断结束 | 另建 session，不能假定已停；检查 state 的 Codex PID，存活则 cleanup/resume blocked | 可能继续服务 durable job；job 未调和前不得停服务或记 cleanup completed |
| 手动提前停止 | 先写当前 attempt `stop-requested`，核验 identity 后向记录的 PGID 发 TERM；等待 current exit 与 complete reconcile，探针语义为 143 | 与 timeout 相同；无安全取消时等待自然最终回执 | 与 timeout 相同，必须实际核验 | 与 timeout 相同；只有 launcher/app-server 停止且 jobs 全部调和后才可清理 |

### 6.1 手动停止 detached launcher

在 dev4 fresh shell 执行；路径和 attempt 必须显式重设。该命令只停止 launcher supervision PGID，不宣称停止 durable jobs/services/app-server：

```bash
set -euo pipefail
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
OUTPUT_ROOT="$ROOT/Rollout_logs/deepcoder_canary_16_v1"
RUN_DIR="$OUTPUT_ROOT/runs/deepcoder_function_16_canary_workspace_v1"
ATTEMPT='<本次 detached supervision attempt>'
ATTEMPT_DIR="$RUN_DIR/meta_session/supervision/$ATTEMPT"
test -d "$ROOT" && test -x "$PYTHON" && test -d "$ATTEMPT_DIR"
test "$(cat "$ATTEMPT_DIR/kind")" = detached
date -u +%Y-%m-%dT%H:%M:%SZ > "$ATTEMPT_DIR/stop-requested.tmp"
mv "$ATTEMPT_DIR/stop-requested.tmp" "$ATTEMPT_DIR/stop-requested"
SUPERVISOR_PID="$("$PYTHON" - "$RUN_DIR" "$ATTEMPT_DIR" <<'PY'
import json
from pathlib import Path
import sys

run_dir = Path(sys.argv[1]).resolve()
attempt_dir = Path(sys.argv[2]).resolve()
identity = json.loads((attempt_dir / "identity.json").read_text())
pid = int(identity["pid"])
if identity["run_dir"] != str(run_dir) or identity["attempt_dir"] != str(attempt_dir):
    raise RuntimeError("supervision attempt path mismatch")
stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
if int(stat[2]) != pid or int(stat[2]) != int(identity["pgid"]):
    raise RuntimeError("supervisor PGID mismatch")
if stat[19] != identity["start_ticks"] or stat[0] == "Z":
    raise RuntimeError("supervisor start identity changed or already exited")
cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
if str(run_dir).encode() not in cmdline or str(attempt_dir).encode() not in cmdline:
    raise RuntimeError("supervisor command identity mismatch")
print(pid)
PY
)"
kill -TERM -- "-$SUPERVISOR_PID"
"$PYTHON" - "$SUPERVISOR_PID" "$ATTEMPT_DIR" <<'PY'
import json
from pathlib import Path
import sys
import time

pid = int(sys.argv[1])
attempt_dir = Path(sys.argv[2])
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    active = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(stat[2]) == pid and stat[0] != "Z":
                active.append(int(entry.name))
        except (FileNotFoundError, PermissionError):
            pass
    reconcile_path = attempt_dir / "reconcile.json"
    if not active and (attempt_dir / "exit").is_file() and reconcile_path.is_file():
        reconciliation = json.loads(reconcile_path.read_text())
        if reconciliation.get("status") != "complete" or reconciliation.get("remaining"):
            raise RuntimeError("launcher PGID reconciliation did not complete")
        print(f"launcher PGID stopped/reconciled; attempt exit={(attempt_dir / 'exit').read_text().strip()}")
        break
    time.sleep(0.5)
else:
    raise RuntimeError("launcher PGID/exit/reconcile not complete; cleanup and resume remain forbidden")
PY
```

若上述等待失败，保持 cleanup/resume blocked 并请求排障；不要改用宽泛 `pkill`、按端口杀进程或其他 run 的 PID。

### 6.2 自包含 cleanup 门

以下块在 **dev4 fresh shell** 自包含重设并校验所有路径。它先验证 config 推导的 output/run/registry 绝对路径，再拒绝在任何 launcher PGID、state launcher/Codex PID、active 或无最终回执 durable job 尚存时继续。变量/identity/exit 缺失、PID 复用或路径不符均 fail closed，不会进入 no-registry 分支：

```bash
set -euo pipefail
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
CONFIG="$ROOT/reproduction/configs/train_4b_deepcoder_canary_workspace.json"
OUTPUT_ROOT="$ROOT/Rollout_logs/deepcoder_canary_16_v1"
RUN_DIR="$OUTPUT_ROOT/runs/deepcoder_function_16_canary_workspace_v1"
ATTEMPT='<本次 supervision attempt>'
ATTEMPT_DIR="$RUN_DIR/meta_session/supervision/$ATTEMPT"
for name in ROOT PYTHON CONFIG OUTPUT_ROOT RUN_DIR ATTEMPT ATTEMPT_DIR; do
  if [ -z "${!name:-}" ]; then
    echo "missing required cleanup variable: $name" >&2
    exit 1
  fi
done
test -d "$ROOT" && test -x "$PYTHON" && test -f "$CONFIG"
test -d "$RUN_DIR" && test -d "$ATTEMPT_DIR"

"$PYTHON" - "$ROOT" "$CONFIG" "$OUTPUT_ROOT" "$RUN_DIR" "$ATTEMPT_DIR" <<'PY'
import json
from pathlib import Path
import sys

root, config_path, output_root, run_dir, attempt_dir = map(Path, sys.argv[1:])
output_root = output_root.resolve()
run_dir = run_dir.resolve()
attempt_dir = attempt_dir.resolve()
config = json.loads(config_path.read_text(encoding="utf-8"))
expected_output = Path(config["output_root"]).resolve()
expected_run = (expected_output / "runs" / config["run_name"]).resolve()
if output_root != expected_output or run_dir != expected_run:
    raise RuntimeError("cleanup config/output/run mismatch")
if not output_root.is_absolute() or not run_dir.is_relative_to(output_root):
    raise RuntimeError("cleanup paths are not the expected absolute descendants")
if attempt_dir.parent.parent != run_dir / "meta_session":
    raise RuntimeError("supervision attempt is outside this run")
if not (attempt_dir / "exit").is_file():
    raise RuntimeError("current supervision attempt has no final exit; cleanup refused")
kind = (attempt_dir / "kind").read_text().strip()

def stat_for(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()

if kind == "detached":
    reconciliation = json.loads((attempt_dir / "reconcile.json").read_text())
    if reconciliation.get("status") != "complete" or reconciliation.get("remaining"):
        raise RuntimeError("launcher PGID reconciliation incomplete; cleanup refused")
    identity = json.loads((attempt_dir / "identity.json").read_text())
    if identity["run_dir"] != str(run_dir) or identity["attempt_dir"] != str(attempt_dir):
        raise RuntimeError("cleanup supervisor identity path mismatch")
    pgid = int(identity["pgid"])
    if pgid != int(identity["pid"]):
        raise RuntimeError("cleanup supervisor PID/PGID mismatch")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = stat_for(int(entry.name))
            if int(stat[2]) == pgid and stat[0] != "Z":
                raise RuntimeError(f"launcher supervision PGID {pgid} still active; cleanup refused")
        except FileNotFoundError:
            pass
    try:
        stat = stat_for(int(identity["pid"]))
    except FileNotFoundError:
        pass
    else:
        if stat[19] != identity["start_ticks"]:
            raise RuntimeError("supervisor PID reused; identity reconciliation required")
else:
    raise RuntimeError("unknown or unsupported supervision kind; only detached is accepted")

state_path = run_dir / "meta_session/state.json"
state = json.loads(state_path.read_text()) if state_path.is_file() else {}
for field in ("launcher_pid", "codex_pid"):
    pid = state.get(field)
    if not pid:
        continue
    try:
        stat = stat_for(int(pid))
    except (FileNotFoundError, ValueError, TypeError):
        continue
    if stat[0] != "Z":
        raise RuntimeError(f"state {field}={pid} is still alive; cleanup refused")

for job_path in sorted((run_dir / "meta_session/jobs").glob("*/job.json")):
    job = json.loads(job_path.read_text())
    result_path = Path(job.get("result_path", "/nonexistent"))
    if result_path.is_file():
        continue
    try:
        stat = stat_for(int(job["pid"]))
        alive = stat[0] != "Z" and stat[19] == job.get("process_start")
    except (FileNotFoundError, ValueError, TypeError, KeyError):
        alive = False
    if alive:
        raise RuntimeError(f"durable job {job.get('job_id')} still active; wait for natural final receipt")
    raise RuntimeError(f"durable job {job.get('job_id')} has no final result and is not identity-reconciled")
print("cleanup preconditions satisfied for the validated config/run/attempt")
PY

# 只有 config 已验证相等后才构造并检查这个预期绝对路径。
REGISTRY="$OUTPUT_ROOT/services/four_gpu_services.json"
test "$REGISTRY" = "$OUTPUT_ROOT/services/four_gpu_services.json"
case "$REGISTRY" in /*) ;; *) echo 'registry path is not absolute' >&2; exit 1;; esac
if [ -e "$REGISTRY" ]; then
  test -f "$REGISTRY" && test ! -L "$REGISTRY"
  phase="$("$PYTHON" - "$REGISTRY" <<'PY'
import json
from pathlib import Path
import sys
print(json.loads(Path(sys.argv[1]).read_text()).get("phase", ""))
PY
)"
  if [ "$phase" != services_stopped ]; then
    PYTHONPATH="$ROOT/RSIFlow/runtime:$ROOT/RSIFlow" "$PYTHON" - "$CONFIG" <<'PY'
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from rsiflow.task_meta.gpu_phases import stop_services

launcher = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
stop_services(SimpleNamespace(output_root=launcher["output_root"]))
PY
  fi
else
  echo "No owned service registry at validated absolute path: $REGISTRY"
fi
```

只有上述门通过、每个 durable job 已有最终回执或明确 identity-checked reconciled 记录、Codex/launcher 均不存活，且 registry 为 `services_stopped` 或在已验证绝对路径确实不存在时，才可记录 **cleanup completed**。清理后核对 registry phase 和 GPU 0–3；cleanup 失败单独记失败，不覆盖原异常。保留所有日志、receipts 和历史 attempts。

## 7. Resume

- Resume 使用**相同 reviewed code、相同 config、相同 run dir、相同 bwrap backend**，但每次必须递增 supervision attempt（如 `0002`），新建对应 `ATTEMPT_DIR` 并使用第 4.1 节的 detached supervisor 命令；不得复用/删除旧 `identity.json`、`reconcile.json`、`exit` 或 `stop-requested`。monitor/cleanup 只接受当前 attempt 的状态。
- 新 attempt 启动前核验上一 attempt exit、complete reconcile、最终 PGID 为空、state、jobs、app-server 和 registry。launcher 或 Codex PID 仍存活时不得另起 attempt；`already_running` 不是完成。
- Active durable job 必须等待自然最终回执，或由仍存在的既有 owner 通过生产 `job_status`/`wait_job` 和 start identity 调和。**未调和前禁止新 resume，也禁止 cleanup completed。** 没有安全取消机制时只等待，不发明 job kill。
- 若所有 jobs 已有最终回执且 launcher/app-server 已停，可按生产恢复路径使用同一 thread/job 记录继续；不要重复 bootstrap、重复 rollout、重放 skill seed。registry 必须属于同一 output root 并明确是复用已验证服务还是已停止后恢复，不能在 active job 使用时清理。
- 每次新 attempt 仍是新的 launcher 前台进程 4 小时监督边界，并需明确授权；它不自动限制独立 session 的 jobs/services/app-server。
- `workflow_policy.json` 只在不存在时创建。新建或既有 incomplete run 的 policy 必须为 `experiment_scope=deepcoder_canary`、rounds=1、schedule=none、retry=2；不要靠修改配置期待覆盖已有 policy，也不要删除它来“重置”实验。历史 complete run 的文件不回写。
- `state.json.status=complete` 的 run 不重新启动。incomplete/disconnected 必须保留原状态和历史，修复对应基础设施问题后再按同路径恢复。
