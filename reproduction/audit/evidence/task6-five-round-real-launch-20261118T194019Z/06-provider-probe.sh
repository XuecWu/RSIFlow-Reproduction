set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
AUDIT_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/evidence/task6-five-round-real-launch-20261118T194019Z
CONFIG="$ROOT/reproduction/configs/train_4b_deepcoder_five_round_workspace.json"
cd "$ROOT"
test "$(git rev-parse HEAD)" = 6c253f98f350f843f746d89a336e18751e8b7a6a
test -z "$(git status --porcelain --untracked-files=normal)"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
set -a
source /data/wuxuecheng/.config/rsiflow/openai.env
set +a
test -n "${OPENAI_API_KEY:-}"
"$PYTHON" -B "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" probe-provider --config "$CONFIG" --output "$AUDIT_DIR/provider-probe.json"
"$PYTHON" -B - "$AUDIT_DIR/provider-probe.json" <<'CHECK'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]);v=json.loads(p.read_text());r=v['response']
print(json.dumps({'evidence':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'http_status':v['http_status'],'endpoint':v['endpoint'],'response_status':r['status'],'response_model':r['model'],'response_id':r.get('id'),'output_present':bool(r.get('output')),'host_identity':v['host_identity']},indent=2))
CHECK
