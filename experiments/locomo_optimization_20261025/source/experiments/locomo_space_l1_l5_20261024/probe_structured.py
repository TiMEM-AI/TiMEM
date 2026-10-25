import httpx,json
from build_hierarchy import *
cfg=space_config();state=read(ROOT/'generated_memories.json');n=next(iter(state['records'].values()))
with httpx.Client(timeout=120,trust_env=False,headers={'X-API-Key':cfg['KEY']}) as c:
 old=next(iter(read(ROOT/'cloud_nodes.json')['nodes'].values()))['cloud_id']
 r=c.get(cfg['SPACE']+'/memories/'+old);write(ROOT/'pilot_detail.json',r.json())
 print('detail',r.status_code,'content_length',len(r.json().get('data',{}).get('content','')),flush=True)
 payload={'content':n['content'],'layer_type':'L1','session_id':RUN+':'+n['conv'],'tags':[RUN+'-structured',n['conv'],n['id']],'keywords':[n['conv']]}
 r=c.post(cfg['SPACE']+'/memories/structured',json=payload);body=r.json();write(ROOT/'structured_pilot.json',body)
 print('structured',r.status_code,json.dumps(body,ensure_ascii=False)[:500],flush=True)
