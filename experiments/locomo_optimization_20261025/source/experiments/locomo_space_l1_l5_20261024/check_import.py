import httpx,json,re
from build_hierarchy import *
n=next(iter(read(ROOT/'generated_memories.json')['records'].values()));v=read(ROOT/'csv_pilot_read.json')['data']['list'][0]
a=re.sub(r'\s+','',n['content']);b=re.sub(r'\s+','',v['summary']);print('summary_whitespace_equal',a==b,'lengths',len(a),len(b),flush=True)
cfg=space_config()
with httpx.Client(timeout=120,trust_env=False,headers={'X-API-Key':cfg['KEY']}) as c:
 r=c.get(cfg['SPACE']+'/credits/balance');write(ROOT/'balance_after_import.json',r.json());print('balance',r.text,flush=True)
 r=c.get(cfg['SPACE']+'/memories/'+read(ROOT/'structured_pilot.json')['data']['memory_id']);print('structured_retry',r.status_code,r.json().get('msg'),flush=True)
