import json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=Path('F:/worktable/research/timem-aml/data/aml_sources.sqlite')
conn=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro',uri=True)
owners={r[0]:r[1] for r in conn.execute('SELECT id,user_id FROM source_turns')}
integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
fts_count=conn.execute('SELECT count(*) FROM source_fts').fetchone()[0]
requests=conn.execute('SELECT count(*) FROM source_requests').fetchone()[0]
conn.close()
hits=json.loads((ROOT/'variant/aml_search.json').read_text(encoding='utf-8'))['results']
source_records=0; refs=0
for hit in hits:
    assert len(hit['data'])<=100
    for row in hit['data']:
        if not row['id'].startswith('src:'): continue
        source_records+=1
        for sid in [row['id']]+row['source_ids']:
            assert owners[sid]==hit['user_id'],(hit['question_id'],sid)
            refs+=1
report={'tested_questions':len(hits),'database_integrity':integrity,'source_turns':len(owners),'fts_turns':fts_count,'requests':requests,'returned_source_records':source_records,'verified_source_references':refs,'cross_user_source_references':0}
(ROOT/'evidence_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
