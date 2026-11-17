# DeepCoder function 16 canary：dev4 执行 runbook

> 状态：**仅完成离线实现、配置、审查和文档；本 runbook 的真实命令尚未执行。**
> 真实执行需要单独的 GO 授权。任何红测试、未关闭的 Critical/Important、目标 HEAD 不一致、凭据缺失或 bwrap 校验失败都阻止启动。

## 1. 范围与证据边界

- 机器配置：`reproduction/configs/train_4b_deepcoder_canary.json`。
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

以下命令在 **dev4 的受控 shell** 中执行。`REVIEWED_HEAD` 由 controller 在 GO 时提供；本文件不写死 HEAD、PID 或动态 TCP。

```bash
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
CONFIG="$ROOT/reproduction/configs/train_4b_deepcoder_canary.json"
OUTPUT_ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/Rollout_logs/deepcoder_canary_16_v1
RUN_DIR="$OUTPUT_ROOT/runs/deepcoder_function_16_canary_v1"
LAUNCH_LOG="$RUN_DIR/meta_session/launcher.log"
REVIEWED_HEAD='<GO 时批准的完整 reviewed commit>'

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

## 4. 新运行：前台或有硬超时的 detached

新运行要求 run dir 不存在；若已存在，停止并转到恢复章节，不能覆盖历史 attempts：

```bash
test ! -e "$RUN_DIR" || { echo 'Run dir exists; use the resume procedure.' >&2; exit 1; }
mkdir -p "$RUN_DIR/meta_session"
```

### 4.1 前台

```bash
set -o pipefail
timeout --signal=TERM --kill-after=5m 4h \
  "$PYTHON" -u "$ROOT/RSIFlow_4B/launch.py" \
  --config "$CONFIG" --run-dir "$RUN_DIR" \
  2>&1 | tee -a "$LAUNCH_LOG"
rc=${PIPESTATUS[0]}
printf '%s\n' "$rc" > "$RUN_DIR/meta_session/dev4-supervisor.exit"
exit "$rc"
```

不要添加 `--prepare-data`；输入已冻结。`timeout` 的 4 小时是外部硬边界，与配置内其他 wall budget 无关。

### 4.2 远端 detached，本机 monitor

当前 CLI 虽有 `--detach`，但它创建的 child 会脱离启动命令。**禁止使用 `timeout 4h … --detach`**：这只限制很快返回的父进程，不能限制 child。以下方式不传给 launcher `--detach`，而是用 `nohup setsid` 包住受 `timeout` 监督的前台 CLI，并保存动态 PID/退出码到本次 run dir：

```bash
nohup setsid bash -c '
  timeout --signal=TERM --kill-after=5m 4h \
    "$PYTHON" -u "$ROOT/RSIFlow_4B/launch.py" \
    --config "$CONFIG" --run-dir "$RUN_DIR"
  rc=$?
  printf "%s\n" "$rc" > "$RUN_DIR/meta_session/dev4-supervisor.exit"
  exit "$rc"
' >> "$LAUNCH_LOG" 2>&1 < /dev/null &
SUPERVISOR_PID=$!
printf '%s\n' "$SUPERVISOR_PID" > "$RUN_DIR/meta_session/dev4-supervisor.pid"
echo "Detached supervisor started; monitor by run dir, not by this terminal."
```

PID 只写入本次运行状态，不复制回维护文档。SSH 断开不应停止该 session；4 小时到期由远端 `timeout` 独立执行。

## 5. 本地 monitor 与状态判定

在本机复用第 2 节的 `DEV4_SSH`，并设置相同路径：

```bash
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
RUN_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/Rollout_logs/deepcoder_canary_16_v1/runs/deepcoder_function_16_canary_v1

# 单次快照
"${DEV4_SSH[@]}" "$PYTHON $ROOT/RSIFlow/monitor_meta.py --run-dir $RUN_DIR --once"
# 持续只读 monitor；Ctrl+C 只退出 monitor，不停止 Meta、rollout 或 GPU 服务。
"${DEV4_SSH[@]}" "$PYTHON $ROOT/RSIFlow/monitor_meta.py --run-dir $RUN_DIR --history 20"
```

重点输出和状态：

- `$RUN_DIR/meta_session/launcher.log`：launcher/Meta 主日志。
- `$RUN_DIR/meta_session/state.json`：`starting/running/disconnected/complete`、thread、PID 和最近错误。
- `$RUN_DIR/meta_session/workflow_state.json`、`RUN_STATUS.md`、`workflow_events.jsonl`：阶段、milestone 和 `finish_allowed`。
- `$RUN_DIR/meta_session/app_server/events.jsonl`：monitor 读取的事件流。
- `$RUN_DIR/meta_session/jobs/`、`controller_transport/`、`controller_receipts/`：持久 job、stdout/stderr 和事实回执。
- `$RUN_DIR/round_1/`、`snapshots/`、`meta/skills.jsonl`、`meta/context.json`：父子轨迹、decision/selection、候选、经验和快照。
- `$OUTPUT_ROOT/services/four_gpu_services.json`：本 output root 拥有的四卡服务 registry。
- `$ROOT/Meta_logs/deepcoder_function_16_canary_v1/`：集中 Meta 记录及部分到 Rollout_logs 的链接。
- `dev4-supervisor.exit`：0 只表示 launcher 返回；124 表示 4 小时 timeout，137 常见于 kill-after 升级。它们都不是模型分数。

`launch_requested`、`already_running`、进程 alive、transport success 或 launcher exit 0 均不等于实验 PASS。完成至少需要 `state.json.status=complete`、`workflow_state.finish_allowed=true`、A0/B1 必需 milestones 齐全，并逐项复核真实回执。若 retain，必须有 terminal-retain 证据且没有后续 attempt/activation/validation；若 accept，必须有 Meta 显式选择和严格正增益的完整同批比较。模型失败、候选执行失败与 infrastructure error 分开报告。

最终报告还要回答：实际组件是什么、16 条父子 ID 是否精确一致、哪些操作 completed/blocked/not run、是否发生自然正例和真实 SFT、是否 retain/accept、是否完成 cleanup。未发生的 MODEL/SFT、五轮、694/744 和 benchmark 必须明确写“未运行”，不能省略成暗示成功。

## 6. 停止与 cleanup

优先让正常控制流执行自身清理。只读 monitor 的 Ctrl+C 不是停止实验的方法。需要提前停止时：

1. 从本次 `dev4-supervisor.pid` 读取 PID，先核对 `/proc` 中 command、process group 和 run dir 身份；只向该记录的 supervisor process group 发 TERM，等待后必要时升级。不得按端口杀进程，不得使用宽泛 `pkill`，不得使用其他 run 的 PID。
2. supervisor 退出后检查 durable jobs 和 registry。若准备 resume，不要误杀仍要接管的 active job；若明确结束，则先让作业停止并保存回执。
3. 若本 output root 的 owned registry 仍存在，在 dev4 执行以下精确清理。它会验证每个 PID identity/PGID/命令归属，只停止本 registry 的服务：

```bash
if [ -f "$OUTPUT_ROOT/services/four_gpu_services.json" ]; then
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
```

没有 registry 时记录“未创建 owned 服务”，不要用端口或 GPU 占用猜测 owner。清理后核对 registry phase、相关进程和 GPU 0–3；清理失败必须单独记为失败，不能被原异常或实验结果掩盖。保留所有日志、receipts 和历史 attempts，不删除失败证据。

## 7. Resume

- Resume 与重新运行不同：使用**相同 reviewed code、相同 config、相同 run dir、相同 bwrap backend**，仍由新的 `timeout 4h` 包裹；每次超出 4 小时后的继续都需要明确授权。
- 先检查 `state.json`、`workflow_state.json`、jobs、registry 和最后回执。若 launcher/job 仍 active，只 monitor 或接管，不重复启动；`already_running` 不是完成。
- 若 launcher 不在但 durable jobs 仍存活，按生产恢复路径重新执行相同前台命令，让它采用同一 thread/job；不要重复 bootstrap、重复 rollout、重放 skill seed 或清理打算接管的 job。
- `workflow_policy.json` 只在不存在时创建。确认既有 policy 仍为 rounds=1、schedule=none、retry=2；不要靠修改配置期待覆盖已有 policy，也不要删除它来“重置”实验。
- `state.json.status=complete` 的 run 不重新启动。incomplete/disconnected 必须保留原状态和历史，修复对应基础设施问题后再按同路径恢复。
