set -euo pipefail
set +x
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -B - <<'CHECK'
import json,subprocess
from pathlib import Path
owner=json.loads(Path('/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json').read_text())
rows={}
for entry in Path('/proc').iterdir():
 if not entry.name.isdigit():continue
 try:
  stat=(entry/'stat').read_text().rsplit(')',1)[1].split()
  rows[int(entry.name)]={'pid':int(entry.name),'ppid':int(stat[1]),'pgid':int(stat[2]),'session':int(stat[3]),'start_ticks':stat[19],'state':stat[0]}
 except FileNotFoundError:continue
desc={owner['pid']}
for _ in range(16):
 add={pid for pid,r in rows.items() if r['ppid'] in desc}
 if add.issubset(desc):break
 desc.update(add)
selected=[]
for pid in sorted(desc):
 r=rows.get(pid,{'pid':pid})
 try:
  base=Path('/proc')/str(pid)
  argv=[a.decode() for a in (base/'cmdline').read_bytes().split(b'\0') if a]
  r['argv']=argv
  r['nspid']=[l for l in (base/'status').read_text().splitlines() if l.startswith(('NSpid:','NSpgid:','NSsid:'))]
  r['pid_namespace']=(base/'ns/pid').readlink().as_posix()
 except FileNotFoundError:r['read_error']='ENOENT'
 selected.append(r)
value={'owner':owner,'descendants':selected,'gpu_apps':subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,gpu_uuid,process_name','--format=csv,noheader,nounits'],text=True,timeout=30)}
print(json.dumps(value,indent=2))
CHECK
