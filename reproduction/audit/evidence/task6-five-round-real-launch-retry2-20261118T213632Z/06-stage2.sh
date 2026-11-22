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
 --config "$CONFIG" --phase stage2 --provider-report "$AUDIT_DIR/provider-probe.json" \
 --occupier-identity /data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json \
 --stage1 "$AUDIT_DIR/stage1.json" --stop-receipt "$AUDIT_DIR/handoff.json" \
 --output "$AUDIT_DIR/stage2.json"
"$PYTHON" -B - "$AUDIT_DIR/stage2.json" "$AUDIT_DIR/handoff.json" <<'CHECK'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]);v=json.loads(p.read_text());stop=json.loads(Path(sys.argv[2]).read_text())
assert v['status']=='success' and v['occupier_stopped'] is True
assert v['stage1_receipt_hash']==stop['stage1_hash']
print(json.dumps({'status':v['status'],'stage':v['stage'],'stage1_receipt_hash':v['stage1_receipt_hash'],'occupier_stopped':v['occupier_stopped'],'gpus':v['gpus'],'receipt_sha256':hashlib.sha256(p.read_bytes()).hexdigest()},indent=2))
CHECK
