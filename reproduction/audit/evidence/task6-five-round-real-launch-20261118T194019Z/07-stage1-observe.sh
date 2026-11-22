set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
AUDIT_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/evidence/task6-five-round-real-launch-20261118T194019Z
CONFIG="$ROOT/reproduction/configs/train_4b_deepcoder_five_round_workspace.json"
cd "$ROOT"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
"$PYTHON" -B "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" observe-preflight \
 --config "$CONFIG" --phase stage1 --provider-report "$AUDIT_DIR/provider-probe.json" \
 --occupier-identity /data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json --output "$AUDIT_DIR/stage1.json"
"$PYTHON" -B - "$AUDIT_DIR" <<'CHECK'
import hashlib,json,sys
from pathlib import Path
out=Path(sys.argv[1]);v=json.loads((out/'stage1.json').read_text())
print(json.dumps({'status':v['status'],'stage':v['stage'],'host':v['host'],'gpus':v['gpus'],'ports':v['ports'],'occupier_running':v['occupier_running'],'evidence':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('stage1*.json')}},indent=2))
CHECK
