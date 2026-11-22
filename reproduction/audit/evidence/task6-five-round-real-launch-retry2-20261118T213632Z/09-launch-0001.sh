set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
AUDIT_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/evidence/task6-five-round-real-launch-retry2-20261118T213632Z
CONFIG="$ROOT/reproduction/configs/train_4b_deepcoder_five_round_workspace.json"
cd "$ROOT"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
set -a
source /data/wuxuecheng/.config/rsiflow/openai.env
set +a
test -n "${OPENAI_API_KEY:-}"
test "$(git rev-parse HEAD)" = 0115b82ef41c7cbccfe37ea688c6a54114ce6d4c
test -z "$(git status --porcelain --untracked-files=normal)"
"$PYTHON" -B "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" launch \
 --config "$CONFIG" --supervision 0001 --timeout 4h --kill-after 5m \
 --preflight-seal "$AUDIT_DIR/prelaunch-input.json"
