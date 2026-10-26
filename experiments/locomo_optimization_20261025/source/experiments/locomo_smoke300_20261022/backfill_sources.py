"""Replay only authenticated historical Add payloads into the new leaf index."""
import hashlib,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BACKEND=Path('F:/worktable/research/timem-aml')
BENCH=Path('F:/worktable/research/bench')
sys.path[:0]=[str(BACKEND),str(BENCH)]
from server.source_evidence import SourceEvidenceStore
from benchmark_data.aml_loaders import load_samples
from benchmark_data.aml_contract import add_body
from benchmark_data.chunking import chunk_messages
store=SourceEvidenceStore()
conn=sqlite3.connect(f'file:{BACKEND.as_posix()}/data/aml_adds.sqlite?mode=ro',uri=True)
counts={}; digests=[]; chunks=0; cross_date=0
for sample in load_samples('locomo_refined'):
    n=0
    for index,chunk in enumerate(chunk_messages(sample.messages)):
        body=add_body(run_id='f1022b',dataset_id='locomo_refined',sample_id=sample.sample_id,chunk_index=index,messages=chunk)
        found=conn.execute('SELECT payload FROM adds WHERE request_id=?',(body['request_id'],)).fetchone()
        assert found,body['request_id']
        prior=json.loads(found[0]); assert prior['success'] and prior['user_id']==body['user_id'] and prior['session_id']==body['session_id']
        store.put(body['user_id'],body['session_id'],body['request_id'],body['messages'])
        n+=len(body['messages']); chunks+=1
        cross_date+=len({r.get('timestamp') for r in body['messages']})>1
        digests.append(hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest())
    counts[sample.sample_id]=n
conn.close()
report={'source_run':'f1022b','chunks':chunks,'cross_timestamp_chunks':cross_date,'messages_by_sample':counts,'messages':sum(counts.values()),'payload_hashes':digests,'source':'only original AML Add fields; no questions, gold, category, evidence IDs or speaker metadata added'}
(ROOT/'source_ingestion.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='payload_hashes'},indent=2))
print('Example grounded evidence:',store.search('eval:f1022b:locomo_refined:conv-26','When did Melanie run a charity race?',1)[0]['content'])
