"""Small controlled smoke and fresh full evaluation of persisted upper memory."""
import argparse, asyncio, collections, hashlib, json, random
import httpx
from upper_w0 import ROOT, PREVIOUS, W0, MODEL, EXPECTED, NAMES, BASELINE, read, write, now, sha, canonical, _score_endpoints
from evaluators.aml_score import _OPEN_ANSWER, _ACCURACY, parse_correct_label

def smoke_questions(questions):
    path=ROOT/'smoke_selection.json'
    if path.exists():
        by_id={q['question_id']:q for q in questions}
        return [by_id[qid] for qid in read(path)['question_ids']]
    rng=random.Random(20261024)
    selected=[]
    for category in ['2','1','4','3']:
        group=[q for q in questions if str(q['category'])==category]
        rng.shuffle(group)
        chosen=[]
        if category=='2':
            for kind in ['week','calendar']:
                matching=[]
                for q in group:
                    req=read(ROOT/'requests/upper_w0'/(q['question_id'].replace('#','_')+'.json'))
                    if any(x['kind']==kind for x in req['diagnostics']['rendered_annotation_trace']):matching.append(q)
                chosen.extend(q for q in matching[:4] if q not in chosen)
        chosen.extend(q for q in group if q not in chosen)
        selected.extend(chosen[:10])
    assert len(selected)==40
    write(path,{'seed':20261024,'per_category':10,'question_ids':[q['question_id'] for q in selected],
        'selection_uses_answers_or_correctness':False,
        'policy':'stratified fixed sample, includes stored calendar/week annotation cases'})
    return selected

def metrics(rows,questions):
    out={}
    for cat in ['2','4','1','3']:
        ids={q['question_id'] for q in questions if str(q['category'])==cat}
        done=[rows[qid] for qid in ids if qid in rows and 'correct' in rows[qid]]
        correct=sum(r['correct'] for r in done)
        out[cat]={'name':NAMES[cat],'completed':len(done),'correct':correct,'total':len(ids),
                  'accuracy':correct/len(ids) if len(done)==len(ids) else None}
    done=[r for r in rows.values() if 'correct' in r]
    return {'completed':len(done),'total':len(questions),'correct':sum(r['correct'] for r in done),
        'accuracy':sum(r['correct'] for r in done)/len(questions) if len(done)==len(questions) else None,
        'categories':out,'no_evidence':sum(not r.get('evidence_nonempty',bool(r.get('evidence'))) for r in done),
        'actual_models':dict(collections.Counter(r[k]['returned_model'] for r in done for k in ['answer_metadata','judge_metadata']))}

async def evaluate(questions,phase,arm,concurrency):
    output=ROOT/phase/arm;output.mkdir(parents=True,exist_ok=True)
    row_dir=output/'rows';row_dir.mkdir(exist_ok=True)
    config=_score_endpoints()
    assert config['answer_model']==config['judge_model']==MODEL
    requests={q['question_id']:read(ROOT/'requests'/arm/(q['question_id'].replace('#','_')+'.json')) for q in questions}
    protocol={'phase':phase,'arm':arm,'model':MODEL,'temperature':0,
        'memory_store_sha256':hashlib.sha256((ROOT/'memories.json').read_bytes()).hexdigest(),
        'request_set_sha256':sha(canonical(requests)),
        'question_ids':[q['question_id'] for q in questions],
        'answer_template_sha256':sha(_OPEN_ANSWER),'judge_template_sha256':sha(_ACCURACY),
        'ranker':'unchanged original BM25 index and frozen candidates',
        'source_reads_at_test_time':False,'cloud_calls':False}
    if (output/'protocol.json').exists():assert read(output/'protocol.json')==protocol
    else:write(output/'protocol.json',protocol)
    rows={}
    for path in row_dir.glob('*.json'):
        row=read(path);rows[row['question_id']]=row
    errors=read(output/'errors.json') if (output/'errors.json').exists() else {}
    sem=asyncio.Semaphore(concurrency);lock=asyncio.Lock();counter=0
    async def checkpoint(qid):
        nonlocal counter
        async with lock:
            if qid in rows:write(row_dir/(qid.replace('#','_')+'.json'),rows[qid])
            write(output/'errors.json',errors)
            counter+=1
            if counter%40==0:write(output/'metrics.json',metrics(rows,questions))
    async with httpx.AsyncClient(timeout=300,trust_env=False) as client:
        async def chat(prompt,role):
            for attempt in range(4):
                try:
                    response=await client.post(config[role+'_base'].rstrip('/')+'/chat/completions',
                        headers={'Authorization':'Bearer '+config[role+'_key']},
                        json={'model':MODEL,'temperature':0,'messages':[{'role':'user','content':prompt}]})
                    response.raise_for_status();body=response.json();message=body['choices'][0]['message']['content']
                    if not str(message).strip():raise ValueError('empty model answer')
                    return str(message).strip(),{'returned_model':body.get('model'),'response_id':body.get('id'),
                        'usage':body.get('usage'),'finish_reason':body['choices'][0].get('finish_reason'),
                        'prompt_sha256':sha(prompt)}
                except Exception:
                    if attempt==3:raise
                    await asyncio.sleep(2**attempt)
        async def one(q):
            qid=q['question_id']
            if 'correct' in rows.get(qid,{}):return
            async with sem:
                try:
                    request=requests[qid]
                    row=rows.setdefault(qid,{'question_id':qid,'category':str(q['category']),'question':q['question'],
                        'started_at':now(),'evidence_sha256':sha(request['evidence']),
                        'evidence_file':str(ROOT/'requests'/arm/(qid.replace('#','_')+'.json')),
                        'selected':request['selected'],'diagnostics':{k:v for k,v in request['diagnostics'].items()
                            if k not in ['rendered_annotation_trace','evidence_row_times']},'evidence_nonempty':bool(request['evidence'])})
                    if 'answer' not in row:
                        row['answer'],row['answer_metadata']=await chat(_OPEN_ANSWER.format(memories=request['evidence'],question=q['question']),'answer')
                        await checkpoint(qid)
                    prompt=_ACCURACY.format(question=q['question'],gold_answer=q['gold'],generated_answer=row['answer'])
                    row['judge'],row['judge_metadata']=await chat(prompt,'judge')
                    row['correct']=parse_correct_label(row['judge']);row['gold']=q['gold'];row['finished_at']=now()
                    errors.pop(qid,None);await checkpoint(qid)
                    count=sum('correct' in r for r in rows.values())
                    if count%10==0:print(phase,arm,'scored',count,'/',len(questions),flush=True)
                except Exception as error:
                    errors[qid]=type(error).__name__+': '+str(error)
                    await checkpoint(qid)
        await asyncio.gather(*(one(q) for q in questions))
    final=metrics(rows,questions);final['errors']=errors
    write(output/'results.json',{'phase':phase,'arm':arm,'results':rows})
    write(output/'metrics.json',final)
    print('RESULT',phase,arm,json.dumps(final,ensure_ascii=False),flush=True)
    assert not errors and final['completed']==len(questions),'Incomplete scoring; resume failed requests'
    return final

async def main(args):
    assert read(ROOT/'storage_audit.json')['passed'] and read(ROOT/'format_audit.json')['passed']
    questions=read(W0/'frozen/questions.json')['questions']
    assert len(questions)==861
    if args.phase=='smoke':
        sample=smoke_questions(questions)
        scores={}
        for arm in ['budget_control','upper_w0']:
            scores[arm]=await evaluate(sample,'smoke',arm,args.concurrency)
        old=read(PREVIOUS/'evaluate_offline/results.partial.json')['results']
        old_smoke=metrics({q['question_id']:old[q['question_id']] for q in sample},sample)
        write(ROOT/'smoke_summary.json',{'created_at':now(),'questions':40,'scores':scores,
            'previous_8000_char_run':old_smoke,'format_checks_passed':True,
            'gate':'complete calls and structural format/provenance checks; no cherry-picking by score',
            'passed':all(s['completed']==40 and not s['errors'] for s in scores.values())})
    else:
        assert read(ROOT/'smoke_summary.json')['passed'],'Smoke has not completed'
        await evaluate(questions,'full','upper_w0',args.concurrency)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['smoke','full']);p.add_argument('--concurrency',type=int,default=24)
    asyncio.run(main(p.parse_args()))
