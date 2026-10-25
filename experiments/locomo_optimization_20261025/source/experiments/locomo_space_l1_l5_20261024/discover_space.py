import ast,json
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parent
t=ast.parse(Path('F:/worktable/doc/export_hierarchy.py').read_text(encoding='utf-8-sig'));cfg={}
for n in t.body:
 if isinstance(n,ast.Assign):
  for a in n.targets:
   if isinstance(a,ast.Name) and a.id in {'SPACE','KEY'}:cfg[a.id]=ast.literal_eval(n.value)
with httpx.Client(timeout=25,trust_env=False) as c:
 for label,method,url,payload in [('openapi','GET','https://api.space.timem.cloud/openapi.json',None),('schema','GET','https://api.space.timem.cloud/v3/api-docs',None),('recall_schema','POST',cfg['SPACE']+'/memories/recall',{})]:
  try:
   r=c.request(method,url,headers={'X-API-Key':cfg['KEY']},json=payload)
   try:d=r.json()
   except Exception:d={'non_json_length':len(r.content)}
   (ROOT/f'discover_{label}.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
   print(json.dumps({'label':label,'status':r.status_code,'response':d if label=='recall_schema' else list(d) if isinstance(d,dict) else type(d).__name__},ensure_ascii=False),flush=True)
  except Exception as exc:print(json.dumps({'label':label,'error':type(exc).__name__}),flush=True)
