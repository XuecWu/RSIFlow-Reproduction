set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
cd "$ROOT"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
printf 'host=%s\n' "$(hostname)"
printf 'utc=%s\n' "$(date -u +%FT%TZ)"
test "$(hostname)" = app-91ba3954af664589900a5228c33cf6f9-695d6797bc-2lk76
test "$(git rev-parse HEAD)" = 6c253f98f350f843f746d89a336e18751e8b7a6a
test -z "$(git status --porcelain --untracked-files=normal)"
printf 'reviewed_head_clean=true\n'
"$PYTHON" -B - <<'CHECK'
import json, os, socket, subprocess, sys
from pathlib import Path
root=Path.cwd();sys.path.insert(0,str(root/'reproduction/scripts'))
from deepcoder_five_round_supervisor import derive_paths,host_identity
config=root/'reproduction/configs/train_4b_deepcoder_five_round_workspace.json'
p=derive_paths(config)
for key in ('output_root','run_dir','central'):
 assert not p[key].exists() and not p[key].is_symlink(), f'not fresh: {p[key]}'
assert p['config']['rounds']==5 and p['config']['retry_rejected_from_round']==6
assert p['config']['meta_skill_seed_path'] is None
print(json.dumps({'host_identity':host_identity(),'fresh_paths':{k:str(p[k]) for k in ('output_root','run_dir','central')},'config':str(config)},indent=2))
identity=Path('/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json')
v=json.loads(identity.read_text());print('occupier_record='+json.dumps(v))
for label,cmd in [('gpus',['nvidia-smi','--query-gpu=index,uuid,name,memory.total,memory.free,memory.used,utilization.gpu','--format=csv,noheader,nounits']),('apps',['nvidia-smi','--query-compute-apps=pid,gpu_uuid','--format=csv,noheader,nounits'])]:
 print(label+'='+subprocess.check_output(cmd,text=True,timeout=30))
for port in (8271,8272,8273,8274):
 with socket.socket() as s:s.bind(('127.0.0.1',port))
print('ports_bind_close=passed')
print('disk_statvfs='+json.dumps({k:getattr(os.statvfs(root),k) for k in ('f_bsize','f_bavail','f_files','f_favail')}))
for name in ('/tmp/rsiflow_4b_meta_worker.sock','/tmp/rsiflow_4b_meta_relay.sock'):
 assert not Path(name).exists(), 'socket occupied: '+name
print('effective_socket_paths_free=true')
CHECK
"$PYTHON" -B RSIFlow_4B/launch.py --config "$ROOT/reproduction/configs/train_4b_deepcoder_five_round_workspace.json" --check
