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
"$PYTHON" -B "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" observe-preflight \
 --config "$CONFIG" --phase stage1 --provider-report "$AUDIT_DIR/provider-probe.json" \
 --occupier-identity /data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json --output "$AUDIT_DIR/stage1.json"
"$PYTHON" -B - "$AUDIT_DIR/stage1.json" <<'CHECK'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]);v=json.loads(p.read_text())
identity=json.loads(Path(v['occupier_identity']['path']).read_text())
print(json.dumps({'stage':v['stage'],'status':v['status'],'host_identity':{k:v[k] for k in ('host','boot_id','pid_namespace','mount_namespace')},'gpu_uuids':v['gpus'],'rank_members':identity['rank_members'],'topology_sha256':identity['topology_sha256'],'evidence_sha256':hashlib.sha256(p.read_bytes()).hexdigest()},indent=2))
CHECK
