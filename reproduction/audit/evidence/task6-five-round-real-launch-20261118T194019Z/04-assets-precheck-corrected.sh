set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
AUDIT_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/evidence/task6-five-round-real-launch-20261118T194019Z
cd "$ROOT"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
set -a
source /data/wuxuecheng/.config/rsiflow/openai.env
set +a
test -n "${OPENAI_API_KEY:-}"
printf 'meta_credential_present=true\n'
"$PYTHON" -B - "$AUDIT_DIR" <<'CHECK'
import hashlib,json,os,sys,time
from pathlib import Path
root=Path.cwd();out=Path(sys.argv[1]);sys.path.insert(0,str(root/'reproduction/scripts'))
from deepcoder_five_round_supervisor import trusted_prelaunch_assets,derive_paths,_normalize_effective,host_identity
from reconcile_deepcoder_run import _observe_owner,_process_record,_group_members_with_errors
config=root/'reproduction/configs/train_4b_deepcoder_five_round_workspace.json'
paths=derive_paths(config)
assets=trusted_prelaunch_assets(config)
def digest(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
reports={}
for name in ['task-5-real-review-codex.md','task-6-launch-hardening-fix4-review-qwen.md','task-6-launch-hardening-fix4-codex-report.md']:
 p=root/'.superpowers/sdd/2026-11-18-deepcoder-five-round-canary'/name
 t=p.read_text()
 if name!='task-6-launch-hardening-fix4-codex-report.md':assert t.startswith('PASS'),name
 reports[name]={'path':str(p),'sha256':digest(p)}
model={}
for version in ('v1','v2'):
 p=root/'Rollout_logs'/('deepcoder_model_smoke_'+version)
 assert p.is_dir(),str(p)
 receipts=p/'receipts'
 model[version]={str(f.relative_to(p)):{'sha256':digest(f),'status':json.loads(f.read_text()).get('status')} for f in sorted(receipts.glob('*.json'))}
assert model['v1'] and model['v2']
effective=_normalize_effective(paths,paths['run_dir']).model_dump()
meta=effective['meta']
assert meta['model']=='gpt-6-astra' and meta['base_url'].rstrip('/')=='http://token.acerobotics.com/v1'
assert meta['api_key_env']=='OPENAI_API_KEY'
assert effective['max_generations']==5 and len(effective['task_replicas'])==4
identity_path=Path('/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json')
owner=json.loads(identity_path.read_text());actual=_process_record(owner['pid'])
assert all(actual[k]==owner[k] for k in ('pid','pgid','start_ticks'))
assert owner['owner']=='dev4-rsiflow-idle-occupier' and owner['gpus']==[0,1,2,3]
assert any('torchrun' in a for a in actual['argv']) and 'train.py' in actual['argv']
observed=_observe_owner(owner,paths['run_dir']);assert observed['state']=='alive',observed
members,errors=_group_members_with_errors(owner['pgid']);assert not errors,(members,errors)
ranks=[]
for entry in Path('/proc').iterdir():
 if not entry.name.isdigit():continue
 try:
  stat=(entry/'stat').read_text().rsplit(')',1)[1].split()
  if int(stat[1])==owner['pid'] and stat[0]!='Z':
   record=_process_record(int(entry.name))
   assert 'train.py' in record['argv'],record
   ranks.append(record)
 except FileNotFoundError:continue
assert len(ranks)==4,ranks
members={'leader_group':members,'rank_children_distinct_groups':ranks}
sockets=[effective['meta'].get('remote_worker_socket'),effective['meta'].get('remote_relay_socket')]
for name in sockets:
 if name:assert not Path(name).exists(),name
private_relays=[str(p) for p in Path('/tmp').glob('rsiflow-meta-relay-*/transport.sock')]
assert not private_relays,private_relays
disk=os.statvfs(root);assert disk.f_bavail*disk.f_frsize>100*1024**3 and disk.f_favail>100000
value={'status':'passed','observed_at':time.time(),'host_identity':host_identity(),'assets':assets,'review_reports':reports,'model_receipts':model,'occupier_identity':owner,'occupier_actual':actual,'occupier_observation':observed,'occupier_members':members,'effective_sockets':sockets,'private_relays':private_relays,'disk_free_bytes':disk.f_bavail*disk.f_frsize,'inode_free':disk.f_favail,'effective_config':effective}
with (out/'asset-precheck.json').open('x') as f:json.dump(value,f,indent=2)
print(json.dumps({'status':'passed','asset_count':len(assets),'evidence':str(out/'asset-precheck.json'),'sha256':digest(out/'asset-precheck.json'),'occupier':actual,'members':members,'disk_free_bytes':value['disk_free_bytes'],'model_receipts':model,'report_hashes':reports},indent=2))
CHECK
