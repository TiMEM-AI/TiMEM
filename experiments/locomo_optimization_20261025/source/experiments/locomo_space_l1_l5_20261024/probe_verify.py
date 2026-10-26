import httpx,json
from build_hierarchy import *
cfg=space_config();b=read(ROOT/'structured_pilot.json');mid=b['data']['memory_id'];n=next(iter(read(ROOT/'generated_memories.json')['records'].values()))
with httpx.Client(timeout=120,trust_env=False,headers={'X-API-Key':cfg['KEY']}) as c:
 r=c.get(cfg['SPACE']+'/memories/'+mid);d=r.json();write(ROOT/'structured_detail.json',d);v=d.get('data',{});print(json.dumps({'status':r.status_code,'keys':list(v),'content_equal':v.get('content')==n['content'],'level':v.get('level'),'tags':v.get('tags'),'billing':b['data'].get('billing')},ensure_ascii=False))
