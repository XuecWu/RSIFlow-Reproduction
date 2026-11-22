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
"$PYTHON" -B "$ROOT/RSIFlow/monitor_meta.py" --run-dir "$RUN_DIR" --once
"$PYTHON" -B "$ROOT/reproduction/scripts/summarize_deepcoder_five_round.py" "$RUN_DIR" --config "$CONFIG"
"$PYTHON" -B - "$ROOT" "$CONFIG" "$RUN_DIR" "$AUDIT_DIR" <<'CHECK'
import hashlib,json,sys,time
from pathlib import Path
root,config,run,out=map(Path,sys.argv[1:]);sys.path.insert(0,str(root/'reproduction/scripts'))
from deepcoder_five_round_supervisor import load_launch_chain,host_identity
from reconcile_deepcoder_run import _process_record,_identity_alive
state=json.loads((run/'meta_session/state.json').read_text())
attempt=run/'meta_session/supervision/0001'
chain=load_launch_chain(attempt,config,run,observer_host=host_identity())
assert chain['binding']['thread_id']==state['thread_id']
assert chain['owners']['launcher']['pid']==state['launcher_pid']
assert chain['owners']['app_server']['pid']==state['codex_pid']
for ident in chain['owners'].values():assert _identity_alive(ident) is True
turns=[]
for p in sorted((run/'meta_session/turns').glob('[0-9]*')):
 command=json.loads((p/'command.json').read_text()) if (p/'command.json').is_file() else {}
 receipt=json.loads((p/'receipt.json').read_text()) if (p/'receipt.json').is_file() else {}
 turns.append({'turn':p.name,'operation':(command.get('request') or {}).get('operation'),'status':receipt.get('status'),'command_path':str(p/'command.json'),'receipt_path':str(p/'receipt.json')})
jobs=[]
for p in sorted((run/'meta_session/jobs').glob('*/job.json')):
 v=json.loads(p.read_text());jobs.append({'path':str(p),'job_id':v.get('job_id'),'operation':(v.get('request') or {}).get('operation'),'pid':v.get('pid'),'result_path':v.get('result_path')})
value={'observed_at':time.time(),'state':state,'launch_chain':{'binding':chain['binding'],'owners':chain['owners'],'expected_gpus':chain['expected_gpus'],'paths':[str(p) for p in chain['paths']],'hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in chain['paths']}},'turns':turns,'jobs':jobs,'services_registry_exists':(Path(config.parent.parent)/'none').exists(),'run_dir':str(run)}
(out/'postlaunch-readonly.json').write_text(json.dumps(value,indent=2))
print(json.dumps({'status':state['status'],'thread_id':state['thread_id'],'launcher_pid':state['launcher_pid'],'app_server_pid':state['codex_pid'],'turns':turns,'jobs':jobs,'identity_chain_hashes':value['launch_chain']['hashes'],'evidence_sha256':hashlib.sha256((out/'postlaunch-readonly.json').read_bytes()).hexdigest()},indent=2))
CHECK
