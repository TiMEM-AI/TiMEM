import httpx,json
from build_hierarchy import *
cfg=space_config();s=read(ROOT/'discover_openapi.json')
for k,v in s['paths'].items():
 if any(x in k for x in ['search','structured','balance','billing','recall']):print('path',k,list(v),flush=True)
with httpx.Client(timeout=120,trust_env=False,headers={'X-API-Key':cfg['KEY']}) as c:
 for name,path,params in [('tag','/memories',{'tag':RUN+'-structured','pageSize':100}),('keyword','/memories',{'keyword':'6279081b','pageSize':100})]:
  r=c.get(cfg['SPACE']+path,params=params);b=r.json();write(ROOT/('structured_read_'+name+'.json'),b);print(name,r.status_code,json.dumps(b,ensure_ascii=False)[:500],flush=True)
 r=c.post(cfg['SPACE']+'/memories/recall',json={'query':RUN+'-structured conv-26 LGBTQ May 8 2023','limit':20});b=r.json();write(ROOT/'structured_recall.json',b);print('recall',r.status_code,json.dumps(b,ensure_ascii=False)[:1500],flush=True)
