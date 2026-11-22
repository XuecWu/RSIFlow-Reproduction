set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
CONFIG="$ROOT/reproduction/configs/train_4b_deepcoder_five_round_workspace.json"
cd "$ROOT"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
"$PYTHON" -B "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" stop --config "$CONFIG" --supervision 0001 --grace 90
