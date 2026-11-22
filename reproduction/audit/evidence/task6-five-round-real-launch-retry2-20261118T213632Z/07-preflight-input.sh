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
"$PYTHON" -B "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" preflight-input \
 --config "$CONFIG" --stage1 "$AUDIT_DIR/stage1.json" --stage2 "$AUDIT_DIR/stage2.json" \
 --output "$AUDIT_DIR/prelaunch-input.json"
"$PYTHON" -B - "$AUDIT_DIR/prelaunch-input.json" "$AUDIT_DIR/stage1.json" "$AUDIT_DIR/stage2.json" <<'CHECK'
import hashlib,json,sys
from pathlib import Path
p,s1,s2=map(Path,sys.argv[1:]);v=json.loads(p.read_text())
assert v['reviewed_commit']=='0115b82ef41c7cbccfe37ea688c6a54114ce6d4c'
assert set(v['assets'])==set(v['required_assets']) and len(v['assets'])==25
assert v['preflight_receipts']['stage1']['sha256']==hashlib.sha256(s1.read_bytes()).hexdigest()
assert v['preflight_receipts']['stage2']['sha256']==hashlib.sha256(s2.read_bytes()).hexdigest()
print(json.dumps({'kind':'prelaunch-input','reviewed_commit':v['reviewed_commit'],'asset_count':len(v['assets']),'stage1_sha256':v['preflight_receipts']['stage1']['sha256'],'stage2_sha256':v['preflight_receipts']['stage2']['sha256'],'seal_file_sha256':hashlib.sha256(p.read_bytes()).hexdigest()},indent=2))
CHECK
