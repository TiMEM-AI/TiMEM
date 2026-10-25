import ast,json,csv,collections,re,hashlib
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parent
t=ast.parse(Path('F:/worktable/doc/export_hierarchy.py').read_text(encoding='utf-8-sig'));cfg={}
for n in t.body:
 if isinstance(n,ast.Assign):
  for a in n.targets:
   if isinstance(a,ast.Name) and a.id in {'SPACE','KEY'}:cfg[a.id]=ast.literal_eval(n.value)
rows=[];pages=[]
with httpx.Client(timeout=40,trust_env=False) as c:
 for page in range(1,101):
  response=c.get(cfg['SPACE']+'/memories',headers={'X-API-Key':cfg['KEY']},params={'page':page,'pageSize':100})
  response.raise_for_status();body=response.json();data=body['data'];part=data['list'];pages.append({'page':page,'n':len(part),'total':data['total'],'pageSize':data['pageSize']});rows+=part
  if len(rows)>=data['total'] or not part:break
  print(f'fetched {len(rows)}/{data["total"]}',flush=True)
unique={str(x['id']):x for x in rows};byconv=collections.defaultdict(collections.Counter)
for x in unique.values():
 m=re.search(r'\bconv-\d+\b',str(x.get('title') or ''))
 if m:byconv[m.group(0)][str(x.get('level'))]+=1
export=list(csv.DictReader(Path('F:/worktable/doc/space_export/hierarchy_all.csv').open(encoding='utf-8-sig',newline='')))
cloudkeys={(str(x.get('level')),str(x.get('title') or ''),str(x.get('content') or '').strip()) for x in unique.values()}
matched=sum((x['level'],x['title'],x['content'].strip()) in cloudkeys for x in export)
report={'api_reported_total':pages[-1]['total'],'unique_records':len(unique),'levels':dict(collections.Counter(str(x.get('level')) for x in unique.values())),'benchmark_by_conv':{k:dict(v) for k,v in byconv.items()},'export_expected':len(export),'exact_export_matches':matched,'pages':pages}
(ROOT/'space_memories.json').write_text(json.dumps({'records':list(unique.values())},ensure_ascii=False,indent=2),encoding='utf-8')
(ROOT/'space_inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
