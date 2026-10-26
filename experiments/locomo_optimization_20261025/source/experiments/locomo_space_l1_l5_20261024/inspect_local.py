"""Read-only comparison with the locally consolidated benchmark hierarchy."""
import json,collections
from pathlib import Path
from dotenv import dotenv_values
import psycopg2
root=Path(__file__).resolve().parent
c=dotenv_values('F:/worktable/research/timem-aml/.env')
db=psycopg2.connect(host=c.get('POSTGRES_HOST','localhost'),port=int(c.get('POSTGRES_PORT',5432)),
    user=c.get('POSTGRES_USER'),password=c.get('POSTGRES_PASSWORD'),dbname=c.get('POSTGRES_DATABASE'),connect_timeout=10)
try:
 with db.cursor() as cur:
  cur.execute("SELECT user_id,level,count(*) FROM core_memories WHERE user_id LIKE %s GROUP BY user_id,level ORDER BY user_id,level",('eval:f1022b:locomo_refined:%',))
  rows=cur.fetchall()
  cur.execute("SELECT p.level,c.level,count(*) FROM memory_child_relations r JOIN core_memories p ON p.id=r.parent_id JOIN core_memories c ON c.id=r.child_id WHERE p.user_id LIKE %s GROUP BY p.level,c.level ORDER BY p.level,c.level",('eval:f1022b:locomo_refined:%',))
  edges=cur.fetchall()
  cur.execute("SELECT count(*) FROM memory_child_relations r JOIN core_memories p ON p.id=r.parent_id JOIN core_memories c ON c.id=r.child_id WHERE p.user_id LIKE %s AND p.user_id<>c.user_id",('eval:f1022b:locomo_refined:%',))
  cross=cur.fetchone()[0]
finally:db.close()
levels=collections.Counter();conv=collections.defaultdict(dict)
for uid,level,n in rows:levels[level]+=n;conv[uid.split(':')[-1]][level]=n
report={'levels':dict(levels),'total':sum(levels.values()),'by_conv':dict(conv),'parent_child_edges':[{'parent':p,'child':c,'n':n} for p,c,n in edges],'cross_user_edges':cross}
(root/'local_inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
