"""Paired evaluation. Identical requests share fresh responses within a phase."""
import argparse, asyncio, collections, hashlib, json, sys
from pathlib import Path
import httpx
import grounded as g
sys.path.insert(0,str(g.PRIOR))
from run_eval import metrics
from evaluators.aml_score import _OPEN_ANSWER,_ACCURACY,parse_correct_label

async def run(args):
    assert g.read(g.ROOT/'input_audit.json')['passed']
    all_questions=g.read(g.W0/'frozen/questions.json')['questions']
    if args.phase=='smoke':
        ids=g.read(g.PRIOR/'smoke_selection.json')['question_ids'];byid={q['question_id']:q for q in all_questions}
        questions=[byid[qid] for qid in ids]
    elif args.phase=='confirm':
        assert g.read(g.ROOT/'full/summary.json')['complete'],'full comparison incomplete'
        questions=[]
        for q in all_questions:
            name=q['question_id'].replace('#','_')+'.json'
            left=g.read(g.ROOT/'requests/lower_only'/name)['evidence']
            right=g.read(g.ROOT/'requests/all_levels'/name)['evidence']
            if left!=right:questions.append(q)
        assert len(questions)==312
    else:
        assert g.read(g.ROOT/'smoke/summary.json')['complete'],'smoke incomplete'
        questions=all_questions
    cfg=g._score_endpoints();assert cfg['answer_model']==cfg['judge_model']==g.MODEL
    folder=g.ROOT/args.phase;folder.mkdir(exist_ok=True)
    cache_dir=folder/'response_cache';cache_dir.mkdir(exist_ok=True)
    protocol={'phase':args.phase,'model':g.MODEL,'temperature':0,'rules':g.RULES,
        'store_sha256':hashlib.sha256((g.ROOT/'store.json').read_bytes()).hexdigest(),
        'index_sha256':hashlib.sha256((g.ROOT/'index.sqlite').read_bytes()).hexdigest(),
        'question_ids':[q['question_id'] for q in questions],
        'answer_template_sha256':g.sha(_OPEN_ANSWER),'judge_template_sha256':g.sha(_ACCURACY),
        'sharing':'only identical full prompts, role, model, temperature and endpoint within this phase; no historical or smoke/full reuse',
        'runtime_L0':False,'cloud_recall':False,
        'code_sha256':{p:hashlib.sha256((g.ROOT/p).read_bytes()).hexdigest() for p in ['grounded.py','evaluate.py','evaluate_confirm.py']}}
    if (folder/'protocol.json').exists():assert g.read(folder/'protocol.json')==protocol
    else:g.write(folder/'protocol.json',protocol)
    inflight={};cache_hits=0;api_calls=0
    async with httpx.AsyncClient(timeout=300,trust_env=False) as client:
        async def chat(prompt,role):
            nonlocal cache_hits,api_calls
            identity={'role':role,'model':g.MODEL,'base':cfg[role+'_base'],'temperature':0,'prompt':prompt}
            key=g.sha(g.canonical(identity));path=cache_dir/(key+'.json')
            if path.exists():
                cache_hits+=1;body=g.read(path)
                return body['text'],{**body['metadata'],'shared':True,'cache_key':key}
            if key in inflight:
                cache_hits+=1;text,metadata=await inflight[key]
                return text,{**metadata,'shared':True,'cache_key':key}
            future=asyncio.get_running_loop().create_future();inflight[key]=future
            try:
                for attempt in range(4):
                    try:
                        response=await client.post(cfg[role+'_base'].rstrip('/')+'/chat/completions',
                            headers={'Authorization':'Bearer '+cfg[role+'_key']},
                            json={'model':g.MODEL,'temperature':0,'messages':[{'role':'user','content':prompt}]})
                        response.raise_for_status();body=response.json();text=str(body['choices'][0]['message']['content']).strip()
                        if not text:raise ValueError('empty response')
                        if role=='judge':parse_correct_label(text)
                        api_calls+=1
                        metadata={'returned_model':body.get('model'),'response_id':body.get('id'),'usage':body.get('usage'),
                            'finish_reason':body['choices'][0].get('finish_reason'),'prompt_sha256':g.sha(prompt),
                            'shared':False,'cache_key':key}
                        g.write(path,{'text':text,'metadata':metadata})
                        future.set_result((text,metadata));return text,metadata
                    except Exception:
                        if attempt==3:raise
                        await asyncio.sleep(2**attempt)
            except Exception as error:
                future.set_exception(error);future.exception();raise
            finally:inflight.pop(key,None)
        outcomes={}
        for arm in args.arms:
            output=folder/arm;output.mkdir(exist_ok=True);rowdir=output/'rows';rowdir.mkdir(exist_ok=True)
            rows={}
            for path in rowdir.glob('*.json'):
                row=g.read(path);rows[row['question_id']]=row
            errors=g.read(output/'errors.json') if (output/'errors.json').exists() else {}
            lock=asyncio.Lock();sem=asyncio.Semaphore(args.concurrency);saved=0
            async def checkpoint(qid):
                nonlocal saved
                async with lock:
                    if qid in rows:g.write(rowdir/(qid.replace('#','_')+'.json'),rows[qid])
                    g.write(output/'errors.json',errors);saved+=1
                    if saved%40==0:g.write(output/'metrics.json',metrics(rows,questions))
            async def one(q):
                qid=q['question_id']
                if 'correct' in rows.get(qid,{}):return
                async with sem:
                    try:
                        path=g.ROOT/'requests'/arm/(qid.replace('#','_')+'.json');request=g.read(path)
                        row=rows.setdefault(qid,{'question_id':qid,'category':str(q['category']),'question':q['question'],
                            'started_at':g.now(),'evidence_file':str(path),'evidence_sha256':g.sha(request['evidence']),
                            'evidence_nonempty':bool(request['evidence']),'navigation':request['navigation'],
                            'diagnostics':request['diagnostics'],'windows':request['windows']})
                        if 'answer' not in row:
                            row['answer'],row['answer_metadata']=await chat(_OPEN_ANSWER.format(memories=request['evidence'],question=q['question']),'answer')
                            await checkpoint(qid)
                        row['judge'],row['judge_metadata']=await chat(_ACCURACY.format(question=q['question'],gold_answer=q['gold'],generated_answer=row['answer']),'judge')
                        row['correct']=parse_correct_label(row['judge']);row['gold']=q['gold'];row['finished_at']=g.now()
                        errors.pop(qid,None);await checkpoint(qid)
                        done=sum('correct' in x for x in rows.values())
                        if done%20==0:print(args.phase,arm,'scored',done,'/',len(questions),flush=True)
                    except Exception as error:
                        errors[qid]=type(error).__name__+': '+str(error);await checkpoint(qid)
            await asyncio.gather(*(one(q) for q in questions))
            result=metrics(rows,questions);result['errors']=errors
            g.write(output/'results.json',{'results':rows});g.write(output/'metrics.json',result)
            print('RESULT',args.phase,arm,json.dumps(result,ensure_ascii=False),flush=True)
            assert not errors and result['completed']==len(questions),'Incomplete arm; resume errors before proceeding'
            outcomes[arm]=result
    complete=all((folder/arm/'results.json').exists() and g.read(folder/arm/'metrics.json')['completed']==len(questions)
        for arm in ['lower_only','all_levels'])
    g.write(folder/'summary.json',{'phase':args.phase,'created_at':g.now(),'complete':complete,'arms_completed_this_call':outcomes,
        'new_api_calls_this_call':api_calls,'identical_request_cache_hits_this_call':cache_hits,
        'response_cache_files':len(list(cache_dir.glob('*.json'))),'same_inputs_shared_only_within_phase':True})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['confirm']);p.add_argument('--concurrency',type=int,default=24)
    p.add_argument('--arms',nargs='+',choices=['lower_only','all_levels'],default=['lower_only','all_levels'])
    asyncio.run(run(p.parse_args()))
