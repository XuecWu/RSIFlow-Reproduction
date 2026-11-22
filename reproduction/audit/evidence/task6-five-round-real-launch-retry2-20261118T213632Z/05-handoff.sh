set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
AUDIT_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/evidence/task6-five-round-real-launch-retry2-20261118T213632Z
cd "$ROOT"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
"$PYTHON" -B "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" handoff \
 --stage1 "$AUDIT_DIR/stage1.json" --output "$AUDIT_DIR/handoff.json"
"$PYTHON" -B - "$AUDIT_DIR/handoff.json" <<'CHECK'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]);v=json.loads(p.read_text())
print(json.dumps({'kind':v['kind'],'stage1_hash':v['stage1_hash'],'host_identity':v['host_identity'],'before':v['before'],'after':v['after'],'receipt_sha256':hashlib.sha256(p.read_bytes()).hexdigest()},indent=2))
assert v['before']['state']=='alive'
assert v['after']['state']=='exited' and v['after']['read_errors']==[]
assert len(v['after']['identities'])==5
assert all(i['alive'] is False for i in v['after']['identities'])
CHECK
