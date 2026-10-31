"""Use the existing deployment's configuration without printing or saving secrets."""
import json, os, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'.testdeps'))
p=subprocess.run(['npx.cmd','--yes','@railway/cli','variables','--project','4d89f1db-7879-45a2-82fd-1c5512efd834','--environment','production','--service','watchtower-mcp','--json'],capture_output=True,text=True,timeout=90)
if p.returncode:raise RuntimeError('Railway configuration lookup failed (contents suppressed)')
values=json.loads(p.stdout)
for k,v in values.items():
    if k.startswith('SUPABASE_DB_') or k=='POLYGON_API_KEY':os.environ[k]=v
from analysis.momentum_attention import read_attention
started=time.monotonic();d=read_attention()
out=ROOT.parent.parent/'artifacts'/'momentum_attention_live.json'
out.write_text(json.dumps(d,indent=2))
print(json.dumps({k:d[k] for k in ['as_of','count','counts','coverage','benchmark']}))
print('Read seconds:',round(time.monotonic()-started,1))
print('First rows:',json.dumps(d['rows'][:3]))
