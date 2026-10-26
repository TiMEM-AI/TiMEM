import httpx,csv,io,json
from build_hierarchy import *
cfg=space_config();n=next(iter(read(ROOT/'generated_memories.json')['records'].values()));title=RUN+'-csv '+n['conv']+' '+n['level']+' '+n['id']
f=io.StringIO();w=csv.DictWriter(f,fieldnames=['content','level','memory_type','title','summary','importance_score','agent_id','subject']);w.writeheader();w.writerow({'content':n['content'],'level':'L1','memory_type':'memory','title':title,'summary':n['content'],'importance_score':0.5,'agent_id':'system','subject':'general'})
with httpx.Client(timeout=180,trust_env=False,headers={'X-API-Key':cfg['KEY']}) as c:
 r=c.post(cfg['SPACE']+'/memories/import',files={'file':('pilot.csv',f.getvalue().encode('utf-8-sig'),'text/csv')});b=r.json();write(ROOT/'csv_pilot_response.json',b);print('import',r.status_code,json.dumps(b,ensure_ascii=False)[:1000],flush=True)
 r=c.get(cfg['SPACE']+'/memories',params={'keyword':title,'pageSize':100});b=r.json();write(ROOT/'csv_pilot_read.json',b);rows=(b.get('data') or {}).get('list',[]);print('verify',r.status_code,'total',len(rows),'content_equal',[x.get('content')==n['content'] for x in rows],'summary_equal',[x.get('summary')==n['content'] for x in rows],flush=True)
