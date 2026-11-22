set -euo pipefail
set +x
ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
PYTHON=/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python
AUDIT_DIR=/data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/audit/evidence/task6-five-round-real-launch-retry2-20261118T213632Z
cd "$ROOT"
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
export PYTHONDONTWRITEBYTECODE=1
"$PYTHON" -B - "$AUDIT_DIR" <<'CHECK'
import hashlib,json,sys
from pathlib import Path
root=Path.cwd();out=Path(sys.argv[1]);sys.path.insert(0,str(root/'reproduction/scripts'))
from deepcoder_five_round_supervisor import derive_paths,trusted_prelaunch_assets
config=root/'reproduction/configs/train_4b_deepcoder_five_round_workspace.json'; paths=derive_paths(config)
assert not any(paths[k].exists() or paths[k].is_symlink() for k in ('output_root','run_dir','central'))
value=json.loads(config.read_text());assert value['rounds']==5 and value['retry_rejected_from_round']==6 and value['meta_skill_seed_path'] is None
assets=trusted_prelaunch_assets(config)
review_paths=[root/'.superpowers/sdd/2026-11-18-deepcoder-five-round-canary'/n for n in ('task-5-real-review-codex.md','task-6-real-layout-fix1-review-qwen.md','task-6-real-layout-fix1-codex-report.md')]
for p in review_paths:
 text=p.read_text()
 if p.name in ('task-5-real-review-codex.md','task-6-real-layout-fix1-review-qwen.md'): assert text.startswith('PASS'),p
reviews={p.name:{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in review_paths}
model={}
for v in ('v1','v2'):
 d=root/'Rollout_logs'/('deepcoder_model_smoke_'+v);assert d.is_dir()
 model[v]={str(f.relative_to(d)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((d/'receipts').glob('*.json'))}
assert model['v1'] and model['v2']
result={'reviewed_head':'0115b82ef41c7cbccfe37ea688c6a54114ce6d4c','fresh_paths':{k:str(paths[k]) for k in ('output_root','run_dir','central')},'scope':{'rounds':5,'unique_tasks':16,'retry_rejected_from_round':6,'seed':42},'assets':assets,'reviews':reviews,'model_v1_receipts':model['v1'],'model_v2_receipts':model['v2']}
p=out/'asset-precheck.json'
with p.open('x') as f:json.dump(result,f,indent=2)
print(json.dumps({'status':'passed','asset_count':len(assets),'reviews':reviews,'model_receipt_count':{k:len(v) for k,v in model.items()},'evidence_path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()},indent=2))
CHECK
"$PYTHON" -B RSIFlow_4B/launch.py --config "$ROOT/reproduction/configs/train_4b_deepcoder_five_round_workspace.json" --check
