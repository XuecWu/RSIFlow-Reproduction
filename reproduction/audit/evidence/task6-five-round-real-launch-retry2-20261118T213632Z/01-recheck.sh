set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
cd "$ROOT"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
test "$(hostname)" = app-91ba3954af664589900a5228c33cf6f9-695d6797bc-2lk76
test "$(git rev-parse HEAD)" = 0115b82ef41c7cbccfe37ea688c6a54114ce6d4c
test -z "$(git status --porcelain --untracked-files=normal)"
"$PYTHON" -B - <<'CHECK'
import json, os, socket, subprocess, sys
from pathlib import Path
root=Path.cwd(); sys.path.insert(0,str(root/'reproduction/scripts'))
from deepcoder_five_round_supervisor import derive_paths,host_identity
from reconcile_deepcoder_run import _process_record,_discover_ranks
config=root/'reproduction/configs/train_4b_deepcoder_five_round_workspace.json'
paths=derive_paths(config)
for k in ('output_root','run_dir','central'):
    assert not paths[k].exists() and not paths[k].is_symlink(), (k,str(paths[k]))
value=json.loads(config.read_text())
assert value['rounds']==5 and value['retry_rejected_from_round']==6 and value['meta_skill_seed_path'] is None
owner_path=Path('/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json')
owner=json.loads(owner_path.read_text()); actual=_process_record(owner['pid'])
assert all(actual[k]==owner[k] for k in ('pid','pgid','start_ticks'))
assert owner['owner']=='dev4-rsiflow-idle-occupier' and owner['gpus']==[0,1,2,3]
assert any('torchrun' in a for a in actual['argv']) and 'train.py' in actual['argv']
ranks=_discover_ranks(owner['pid'],'/data/wuxuecheng/z-occupier-ready/train.py')
assert len(ranks)==4 and sorted(r['local_rank']['LOCAL_RANK'] for r in ranks)==['0','1','2','3']
gpu=subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,name,memory.total,memory.free,memory.used,utilization.gpu','--format=csv,noheader,nounits'],text=True,timeout=30)
apps=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,gpu_uuid','--format=csv,noheader,nounits'],text=True,timeout=30)
for port in (8271,8272,8273,8274):
    with socket.socket() as s:s.bind(('127.0.0.1',port))
sockets=[Path('/tmp/rsiflow_4b_meta_worker.sock'),Path('/tmp/rsiflow_4b_meta_relay.sock')]
for p in sockets: assert not p.exists(),str(p)
disk=os.statvfs(root)
print(json.dumps({'host_identity':host_identity(),'config':str(config),'fresh_paths':{k:str(paths[k]) for k in ('output_root','run_dir','central')},
 'rounds':value['rounds'],'retry':value['retry_rejected_from_round'],'rank_records':ranks,'occupier':actual,
 'gpu_csv':gpu,'compute_apps_csv':apps,'ports_bind_close':'passed','socket_paths_free':'passed',
 'disk_free_bytes':disk.f_bavail*disk.f_frsize,'inode_free':disk.f_favail},indent=2))
CHECK
