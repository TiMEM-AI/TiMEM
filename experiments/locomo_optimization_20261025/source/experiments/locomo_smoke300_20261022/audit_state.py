import hashlib,json,sys
from pathlib import Path
import psycopg2
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parent
BACKEND=Path('F:/worktable/research/timem-aml')
cfg=dotenv_values(BACKEND/'.env')
conn=psycopg2.connect(host=cfg.get('POSTGRES_HOST','localhost'),port=int(cfg.get('POSTGRES_PORT','5432')),user=cfg.get('POSTGRES_USER','timem_user'),password=cfg.get('POSTGRES_PASSWORD','timem_password'),dbname=cfg.get('POSTGRES_DATABASE','timem_db'),connect_timeout=5)
try:
    with conn.cursor() as cur:
        cur.execute("SELECT id,user_id,level,content FROM core_memories WHERE user_id LIKE %s ORDER BY user_id,id",('eval:f1022b:locomo_refined:%',))
        rows=cur.fetchall()
finally: conn.close()
files=['server/app.py','server/engine.py','server/search_expand.py','server/time_anchor.py','server/source_evidence.py','config/settings.yaml','config/prompts.yaml','config/datasets/default/retrieval_config.yaml']
state={'core_rows':len(rows),'core_content_sha256':hashlib.sha256(json.dumps(rows,default=str,ensure_ascii=False).encode()).hexdigest(),'files':{f:hashlib.sha256((BACKEND/f).read_bytes()).hexdigest() for f in files},'source_index_sha256':hashlib.sha256((BACKEND/'data/aml_sources.sqlite').read_bytes()).hexdigest()}
label=sys.argv[1] if len(sys.argv)>1 else 'midrun'
(ROOT/f'audit.{label}.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
print(json.dumps({'core_rows':state['core_rows'],'core_content_sha256':state['core_content_sha256'],'label':label}))
