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
"$PYTHON" -B "$ROOT/reproduction/scripts/deepcoder_five_round_supervisor.py" status --config "$CONFIG" --supervision 0001
"$PYTHON" -B "$ROOT/reproduction/scripts/reconcile_deepcoder_run.py" inspect --config "$CONFIG" --run-dir "$RUN_DIR" --supervision 0001
"$PYTHON" -B - "$RUN_DIR" "$AUDIT_DIR" <<'CHECK'
import hashlib,json,sys
from pathlib import Path
run,out=map(Path,sys.argv[1:]);attempt=run/'meta_session/supervision/0001'
turns=[]
for p in sorted((run/'meta_session/turns').glob('[0-9]*')):
 c=json.loads((p/'command.json').read_text()) if (p/'command.json').is_file() else {}
 r=json.loads((p/'receipt.json').read_text()) if (p/'receipt.json').is_file() else {}
 turns.append({'turn':p.name,'request':c.get('request'),'receipt':r})
state=json.loads((run/'meta_session/state.json').read_text())
registry=run.parent.parent/'services/four_gpu_services.json'
value={'state':state,'turns':turns,'jobs':[str(p) for p in (run/'meta_session/jobs').glob('*/job.json')],
       'phases':[str(p) for p in run.glob('round_*/attempts/attempt_*/**/gpu_phase_receipt.json')],
       'registry_path':str(registry),'registry_exists':registry.exists(),
       'central_path':str(run.parent.parent.parent/'Meta_logs'/run.name),'central_exists':(run.parent.parent.parent/'Meta_logs'/run.name).exists(),
       'supervision':{n:hashlib.sha256((attempt/n).read_bytes()).hexdigest() for n in ('identity.json','launch.json','post-launch-identity.json','launch-final.json') if (attempt/n).is_file()}}
p=out/'abnormal-inspect.json';p.write_text(json.dumps(value,indent=2));print(json.dumps({'state_status':state.get('status'),'thread_id':state.get('thread_id'),'stages':state.get('current_stage'),'turns':[{k:t[k] for k in ('turn','request') } | {'status':t['receipt'].get('status'),'error':t['receipt'].get('error')} for t in turns], 'jobs':value['jobs'],'phases':value['phases'],'registry_exists':value['registry_exists'],'central_exists':value['central_exists'],'evidence_path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()},indent=2))
CHECK
