set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
AUDIT_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/evidence/task6-five-round-real-launch-retry2-20261118T213632Z
CONFIG="$ROOT/reproduction/configs/train_4b_deepcoder_five_round_workspace.json"
RUN_DIR="$ROOT/Rollout_logs/deepcoder_five_round_16_v1/runs/deepcoder_function_16_five_round_workspace_v1"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
cd "$ROOT"
"$PYTHON" -B "$ROOT/reproduction/scripts/reconcile_deepcoder_run.py" inspect --config "$CONFIG" --run-dir "$RUN_DIR" --supervision 0001 > "$AUDIT_DIR/final-inspect.json"
"$PYTHON" -B "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" status --config "$CONFIG" --supervision 0001 > "$AUDIT_DIR/final-supervision-status.json"
"$PYTHON" -B - "$RUN_DIR" "$AUDIT_DIR" <<'CHECK'
import hashlib,json,subprocess,sys,time
from pathlib import Path
run,out=map(Path,sys.argv[1:]);receipts=sorted((run/'meta_session/supervision/0001/external-cleanup').glob('*/receipt.json'))
assert receipts
receipt_path=receipts[-1];receipt=json.loads(receipt_path.read_text())
state=json.loads((run/'meta_session/state.json').read_text())
registry=run.parent.parent/'services/four_gpu_services.json'
apps=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,gpu_uuid','--format=csv,noheader,nounits'],text=True,timeout=30)
gpu=subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,name,memory.total,memory.free,memory.used,utilization.gpu','--format=csv,noheader,nounits'],text=True,timeout=30)
value={'observed_at':time.time(),'run_dir':str(run),'state':state,'cleanup_receipt_path':str(receipt_path),'cleanup_receipt_sha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest(),'cleanup_status':receipt.get('status'),'cleanup_blockers':receipt.get('blockers'),'cleanup_errors':receipt.get('errors'),'cleanup_owners':receipt.get('owners'),'cleanup_registry':receipt.get('registry'),'supervision':json.loads((out/'final-supervision-status.json').read_text()),'jobs':[str(p) for p in (run/'meta_session/jobs').glob('*/job.json')],'training_phases':[str(p) for p in run.glob('round_*/attempts/attempt_*/**/gpu_phase_receipt.json')],'registry_exists':registry.exists(),'compute_apps_csv':apps,'gpu_csv':gpu,'occupier_identity_exists':Path('/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json').exists(),'restore_performed':False}
p=out/'final-pending-state.json';p.write_text(json.dumps(value,indent=2));print(json.dumps({'cleanup_status':value['cleanup_status'],'blockers':value['cleanup_blockers'],'state_status':state.get('status'),'current_stage':state.get('current_stage'),'thread_id':state.get('thread_id'),'jobs':value['jobs'],'training_phases':value['training_phases'],'registry_exists':value['registry_exists'],'compute_apps_csv':apps,'gpu_csv':gpu,'receipt_path':str(receipt_path),'receipt_sha256':value['cleanup_receipt_sha256'],'final_evidence':str(p),'final_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'restore_performed':False},indent=2))
CHECK
