import httpx,json,csv,io
from build_hierarchy import *
cfg=space_config();n=next(iter(read(ROOT/'generated_memories.json')['records'].values()))
with httpx.Client(timeout=120,trust_env=False,headers={'X-API-Key':cfg['KEY']}) as c:
 r=c.get(cfg['SPACE']+'/memories/import/template');(ROOT/'import_template.csv').write_bytes(r.content);print('template',r.status_code,r.text[:1400],flush=True)
 for suffix in ['/credits/balance','/credits/rules']:
  r=c.get(cfg['SPACE']+suffix);write(ROOT/('space_'+suffix.split('/')[-1]+'.json'),r.json());print(suffix,r.status_code,r.text[:1600],flush=True)
