set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
AUDIT_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/evidence/task6-five-round-real-launch-20261118T194019Z
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1
"$PYTHON" -B - "$AUDIT_DIR" <<'CHECK'
import hashlib,json,subprocess,sys,time
from pathlib import Path
root=Path.cwd();out=Path(sys.argv[1]);sys.path.insert(0,str(root/'reproduction/scripts'))
from deepcoder_five_round_supervisor import derive_paths,host_identity
from reconcile_deepcoder_run import _process_record,_observe_owner
config=root/'reproduction/configs/train_4b_deepcoder_five_round_workspace.json';paths=derive_paths(config)
cmd=['nvidia-smi','--query-compute-apps=pid,uuid','--format=csv,noheader,nounits']
result=subprocess.run(cmd,text=True,capture_output=True,timeout=30)
owner_path=Path('/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json')
owner=json.loads(owner_path.read_text());actual=_process_record(owner['pid'])
assert all(actual[k]==owner[k] for k in ('pid','pgid','start_ticks'))
expected=paths['run_dir']/'meta_session/supervision/0001'
v={'status':'BLOCKED','current_stage':'preflight_stage1_gpu_observation_failed','time':time.time(),'host_identity':host_identity(),'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'git_status':subprocess.check_output(['git','status','--porcelain'],text=True),'failed_command':{'argv':cmd,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr},'occupier_record':owner,'occupier_actual':actual,'occupier_observation':_observe_owner(owner,paths['run_dir']),'occupier_identity_sha256':hashlib.sha256(owner_path.read_bytes()).hexdigest(),'gpu_csv':subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,name,memory.total,memory.free,memory.used,utilization.gpu','--format=csv,noheader,nounits'],text=True,timeout=30),'output_exists':paths['output_root'].exists(),'run_exists':paths['run_dir'].exists(),'central_exists':paths['central'].exists(),'attempt_exists':expected.exists(),'thread_id':None,'provider_probe':'passed','stage1_receipt_exists':(out/'stage1.json').exists(),'handoff_performed':False,'stage2_performed':False,'launch_performed':False,'cleanup_performed':False,'restore_performed':False,'run_dir':str(paths['run_dir']),'attempt_dir':str(expected)}
with (out/'blocked-final-state.json').open('x') as f:json.dump(v,f,indent=2)
print(json.dumps(v,indent=2))
print('final_state_sha256='+hashlib.sha256((out/'blocked-final-state.json').read_bytes()).hexdigest())
CHECK
