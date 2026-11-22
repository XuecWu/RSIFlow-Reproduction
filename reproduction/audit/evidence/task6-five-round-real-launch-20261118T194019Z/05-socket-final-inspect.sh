set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
AUDIT_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/evidence/task6-five-round-real-launch-20261118T194019Z
cd "$ROOT"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
"$PYTHON" -B - "$AUDIT_DIR" <<'CHECK'
import hashlib,json,os,socket,subprocess,sys,time
from pathlib import Path
root=Path.cwd();out=Path(sys.argv[1]);sys.path.insert(0,str(root/'reproduction/scripts'))
from deepcoder_five_round_supervisor import derive_paths,host_identity,trusted_prelaunch_assets
from reconcile_deepcoder_run import _observe_owner,_process_record
paths=derive_paths(root/'reproduction/configs/train_4b_deepcoder_five_round_workspace.json')
assets=trusted_prelaunch_assets(paths['config_path'])
net=Path('/proc/net/unix').read_text().splitlines()
private=[str(p) for p in Path('/tmp').glob('rsiflow-meta-relay-*/transport.sock')]
entries=[]
for name in private:
 rows=[line.split() for line in net if line.split()[-1:]==[name]]
 inodes={r[6] for r in rows if len(r)>=8}
 owners=[]
 for proc in Path('/proc').iterdir():
  if not proc.name.isdigit():continue
  try:
   fds=list((proc/'fd').iterdir())
  except (FileNotFoundError,PermissionError):continue
  matched=[]
  for fd in fds:
   try:
    link=os.readlink(fd)
    if link.startswith('socket:[') and link[8:-1] in inodes:matched.append(fd.name)
   except (FileNotFoundError,PermissionError):continue
  if matched:
   try:
    stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
    raw=(proc/'cmdline').read_bytes();argv=[v.decode(errors='replace') for v in raw.split(b'\0') if v]
    owned_paths=[v for v in argv if v.startswith('/data/wuxuecheng/Hierarchical_level/RSI-Flow/') and ('/runs/' in v or v.endswith('.py'))]
    owners.append({'pid':int(proc.name),'ppid':int(stat[1]),'pgid':int(stat[2]),'start_ticks':stat[19],'state':stat[0],'socket_fds':matched,'executable':Path(argv[0]).name if argv else '', 'rsiflow_paths':owned_paths,'argv_sha256':hashlib.sha256(raw).hexdigest()})
   except FileNotFoundError:pass
 entries.append({'path':name,'proc_net_unix':rows,'owners':owners})
owner_path=Path('/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json');owner=json.loads(owner_path.read_text())
actual=_process_record(owner['pid']);assert all(actual[k]==owner[k] for k in ('pid','pgid','start_ticks'))
value={'status':'blocked_preflight','observed_at':time.time(),'host_identity':host_identity(),'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'worktree_status':subprocess.check_output(['git','status','--porcelain'],text=True),'assets':assets,'private_relays':entries,'occupier_record':owner,'occupier_actual':actual,'occupier_observation':_observe_owner(owner,paths['run_dir']),'occupier_identity_sha256':hashlib.sha256(owner_path.read_bytes()).hexdigest(),'paths':{k:{'path':str(paths[k]),'exists':paths[k].exists(),'is_symlink':paths[k].is_symlink()} for k in ('output_root','run_dir','central')},'gpu_csv':subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,name,memory.total,memory.free,memory.used,utilization.gpu','--format=csv,noheader,nounits'],text=True,timeout=30),'compute_apps_csv':subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,gpu_uuid','--format=csv,noheader,nounits'],text=True,timeout=30),'provider_probe_performed':False,'handoff_performed':False,'launch_performed':False}
with (out/'final-inspection.json').open('x') as f:json.dump(value,f,indent=2)
print(json.dumps({k:v for k,v in value.items() if k!='assets'},indent=2))
print('asset_count='+str(len(assets)))
print('evidence_sha256='+hashlib.sha256((out/'final-inspection.json').read_bytes()).hexdigest())
CHECK
