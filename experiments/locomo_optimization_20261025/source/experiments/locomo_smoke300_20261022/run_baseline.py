import asyncio, hashlib, json, logging, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
BENCH = Path('F:/worktable/research/bench')
sys.path.insert(0, str(BENCH))
os.environ['NO_PROXY'] = '127.0.0.1,localhost,::1'
from runners.aml_run import run_search_and_score
from runners import aml_run
aml_run.SAMPLE_SEARCH_CONCURRENCY = 4

def prepare():
    manifest = json.loads((BENCH/'reports/f1022b/aml_questions.json').read_text(encoding='utf-8'))
    quotas = {'2':100, '1':77, '4':98, '3':25}
    chosen=[]
    for cat, n in quotas.items():
        pool=[r for r in manifest['questions'] if str(r['category'])==cat]
        pool.sort(key=lambda r: hashlib.sha256(('timem-smoke300-v1:'+r['question_id']).encode()).hexdigest())
        chosen.extend(pool[:n])
    ids={r['question_id'] for r in chosen}
    manifest['questions']=[r for r in manifest['questions'] if r['question_id'] in ids]
    assert len(ids)==300 and len({r['sample_id'] for r in chosen})==10
    (ROOT/'selection.json').write_text(json.dumps({'results':[{'question_id':r['question_id']} for r in manifest['questions']]},indent=2),encoding='utf-8')
    (ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Fixed selection:',quotas,flush=True)

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    prepare()
    result=asyncio.run(run_search_and_score(run_id='f1022b',output_dir=str(ROOT/'baseline'),only_from=str(ROOT/'selection.json')))
    print('BASELINE COMPLETE',result['score']['correct'],result['score']['total'],flush=True)
