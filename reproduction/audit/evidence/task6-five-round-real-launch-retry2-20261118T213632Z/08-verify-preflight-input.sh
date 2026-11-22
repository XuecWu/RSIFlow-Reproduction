set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
AUDIT_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/evidence/task6-five-round-real-launch-retry2-20261118T213632Z
cd "$ROOT"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
"$PYTHON" -B - "$AUDIT_DIR/prelaunch-input.json" "$AUDIT_DIR/stage1.json" "$AUDIT_DIR/stage2.json" <<'CHECK'
import hashlib,json,sys
from pathlib import Path
p,s1,s2=map(Path,sys.argv[1:]);v=json.loads(p.read_text())
assert v['reviewed_commit']=='0115b82ef41c7cbccfe37ea688c6a54114ce6d4c'
assert len(v['assets'])==25
assert v['preflight_receipts']['stage1']['sha256']==hashlib.sha256(s1.read_bytes()).hexdigest()
assert v['preflight_receipts']['stage2']['sha256']==hashlib.sha256(s2.read_bytes()).hexdigest()
assert json.loads(s1.read_text())['status']=='success' and json.loads(s2.read_text())['status']=='success'
print(json.dumps({'kind':'prelaunch-input','reviewed_commit':v['reviewed_commit'],'asset_count':len(v['assets']),'stage1_sha256':v['preflight_receipts']['stage1']['sha256'],'stage2_sha256':v['preflight_receipts']['stage2']['sha256'],'seal_file_sha256':hashlib.sha256(p.read_bytes()).hexdigest()},indent=2))
CHECK
"$PYTHON" -B RSIFlow_4B/launch.py --config "$ROOT/reproduction/configs/train_4b_deepcoder_five_round_workspace.json" --check
