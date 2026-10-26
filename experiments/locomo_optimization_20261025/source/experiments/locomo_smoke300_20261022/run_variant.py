"""Paired ablation: share each fresh baseline recall, add production source grounding."""
import asyncio,hashlib,json,logging,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BENCH=Path('F:/worktable/research/bench')
BACKEND=Path('F:/worktable/research/timem-aml')
sys.path[:0]=[str(BACKEND),str(BENCH)]
os.environ['NO_PROXY']='127.0.0.1,localhost,::1'
from server.source_evidence import SourceEvidenceStore,ground_evidence
from runners.aml_run import _score_question_row,_score_endpoints
from evaluators.aml_score import format_memories

def read(path,default=None):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError,json.JSONDecodeError): return default
def write(path,data):
    temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(path)

async def main():
    manifest=read(ROOT/'manifest.json')
    questions={r['question_id']:r for r in manifest['questions']}
    directory=ROOT/'variant'; directory.mkdir(exist_ok=True)
    write(directory/'aml_questions.json',manifest)
    scored={r['question_id']:r for r in read(directory/'aml_score.partial.json',{'results':[]})['results']}
    hits={r['question_id']:r for r in read(directory/'aml_search.json',{'results':[]})['results']}
    endpoints=_score_endpoints(); assert endpoints['answer_model']==endpoints['judge_model']=='deepseek-v4.1-flash'
    source=SourceEvidenceStore(); semaphore=asyncio.Semaphore(4); lock=asyncio.Lock()
    pending={}; started=time.time()
    provenance={'mode':'paired post-retrieval ablation, identical fresh current-model baseline candidates for both arms',
        'source_sha256':hashlib.sha256((BACKEND/'server/source_evidence.py').read_bytes()).hexdigest(),
        'source_database_sha256':hashlib.sha256(Path(source.path).read_bytes()).hexdigest(),
        'answer_model':endpoints['answer_model'],'judge_model':endpoints['judge_model'],'temperature':0}
    write(ROOT/'experiment.json',provenance)
    def report():
        rows=[scored[k] for k in questions if k in scored]
        return {**provenance,'complete':len(rows)==300,'total':len(rows),'correct':sum(bool(r['correct']) for r in rows),'results':rows}
    async def one(base):
        qid=base['question_id']; row=questions[qid]
        async with semaphore:
            if qid not in hits:
                start=time.perf_counter()
                data=await asyncio.to_thread(ground_evidence,base['user_id'],row['question'],base['data'],100,store=source)
                elapsed=(time.perf_counter()-start)*1000
                hits[qid]={**base,'data':data,'source_grounding_ms':elapsed,'baseline_latency_ms':base.get('latency_ms'),
                    'latency_ms':(base.get('latency_ms') or 0)+elapsed}
                write(directory/'aml_search.json',{'results':[hits[k] for k in questions if k in hits]})
            hit=hits[qid]
            memories=format_memories(hit['data'],question=row['question'],options=row.get('options'))
            for attempt in range(3):
                try:
                    result=await _score_question_row(row,memories,**endpoints,hit=hit)
                    if result['method']=='judge_parse_error': raise RuntimeError('judge parse failure')
                    break
                except Exception:
                    if attempt==2: raise
                    await asyncio.sleep(3)
            async with lock:
                scored[qid]=result
                write(directory/'aml_score.partial.json',report())
                print(f"variant {len(scored)}/300 {qid} correct={result['correct']}",flush=True)
    while len(scored)<300:
        for qid, task in list(pending.items()):
            if task.done():
                await task
                del pending[qid]
        saved=read(ROOT/'baseline/aml_search.json',{'results':[]})
        for hit in saved['results']:
            qid=hit['question_id']
            if qid in questions and qid not in scored and qid not in pending:
                pending[qid]=asyncio.create_task(one(hit))
        if time.time()-started>14400: raise TimeoutError('baseline did not complete within four hours')
        await asyncio.sleep(3)
    await asyncio.gather(*pending.values())
    write(directory/'aml_score.json',report())
    print('VARIANT COMPLETE',report()['correct'],300,flush=True)

if __name__=='__main__':
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
