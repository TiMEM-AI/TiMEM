"""Fresh 861-answer evaluation, resumable and isolated from L0.

Cloud mode uses native Space recall and only verified cloud summary fields.
Offline mode is an explicitly different local BM25 experiment, never a cloud score.
"""
import argparse, asyncio, collections, hashlib, json, math, re, uuid
from pathlib import Path
import httpx
from build_hierarchy import ROOT, RUN, MODEL, BENCH, read, write, space_config, now, _score_endpoints
from cloud_pipeline import unwrap, normalized
from evaluators.aml_score import _OPEN_ANSWER, _ACCURACY, parse_correct_label

QUESTIONS = ROOT.parent/'locomo_temporal_week_20261023/frozen/questions.json'
EXPECTED = {'1':77,'2':167,'3':36,'4':581}
BASELINE = {'1':48,'2':118,'3':29,'4':534}
NAMES = {'1':'多跳','2':'时间','3':'推理','4':'单跳'}

def digest(s):
    return hashlib.sha256(s.encode()).hexdigest()

def tokens(s):
    return re.findall(r"[a-z0-9]+",s.lower())

class LocalBM25:
    def __init__(self, records):
        self.records = records
        self.groups = collections.defaultdict(list)
        self.tf = {}
        for nid,n in records.items():
            self.groups[n['conv']].append(nid)
            self.tf[nid] = collections.Counter(tokens(n['content']))
        self.df = {}
        for conv,ids in self.groups.items():
            counts = collections.Counter()
            for nid in ids:
                counts.update(self.tf[nid].keys())
            self.df[conv] = counts
    def search(self, conv, question):
        ids = self.groups[conv]
        avg = sum(sum(self.tf[nid].values()) for nid in ids)/len(ids)
        scored = []
        for nid in ids:
            tf = self.tf[nid]; size = sum(tf.values()); score = 0
            for term in set(tokens(question)):
                frequency = tf[term]
                if frequency:
                    df = self.df[conv][term]
                    idf = math.log(1+(len(ids)-df+0.5)/(df+0.5))
                    score += idf*frequency*2.2/(frequency+1.2*(0.25+0.75*size/avg))
            scored.append((score,nid))
        return [{'id':nid,'score':score} for score,nid in sorted(scored,reverse=True)[:100] if score>0]

def pack(hits, by_id):
    selected = []; seen = set(); lines = []; used = 0
    for h in hits:
        n = by_id[h['id']]
        key = normalized(n['content'])
        if key in seen:
            continue
        seen.add(key)
        line = f"- [{n['start']}] {n['content']}"
        # Same 8,000-character evidence cap as the previous W0 test.
        if used+len(line)>8000:
            remaining = 8000-used
            if remaining<200:
                break
            line = line[:remaining]
        lines.append(line); used += len(line)+1
        selected.append({'id':h['id'],'node_id':n.get('node_id',n.get('id')),
            'level':n['level'],'conv':n['conv'],'score':h.get('score'),
            'content_sha256':digest(n['content']),'evidence_characters':len(line)})
        if used>=8000 or len(selected)>=12:
            break
    return '\n'.join(lines) if lines else '(no memories)', selected

def report(state, output):
    rows = state['results']
    scores = {}
    for category,total in EXPECTED.items():
        subset = [r for r in rows.values() if r['category']==category and 'correct' in r]
        correct = sum(r['correct'] for r in subset)
        scores[category] = {'name':NAMES[category], 'completed':len(subset),'total':total,
            'correct':correct, 'accuracy':correct/total if len(subset)==total else None,
            'baseline_correct':BASELINE[category], 'baseline_accuracy':BASELINE[category]/total,
            'delta_pp':100*(correct-BASELINE[category])/total if len(subset)==total else None}
    done = sum('correct' in r for r in rows.values())
    correct = sum(r.get('correct',False) for r in rows.values())
    metrics = {'run':RUN,'retrieval':state['retrieval'],'completed':done,'total':861,
        'correct':correct,'accuracy':correct/861 if done==861 else None,'categories':scores,
        'baseline_correct':729,'baseline_accuracy':729/861,
        'delta_pp':100*(correct-729)/861 if done==861 else None,
        'evidence_levels':dict(collections.Counter(x['level'] for r in rows.values() for x in r.get('selected',[]))),
        'no_evidence':sum(not r.get('selected') for r in rows.values()),
        'errors':state['errors']}
    write(output/'metrics.json',metrics)
    return metrics

async def main(args):
    questions = read(QUESTIONS)['questions']
    assert len(questions)==861 and dict(collections.Counter(str(q['category']) for q in questions))==EXPECTED
    plan = read(ROOT/'hierarchy_plan.json')
    generated = read(ROOT/'generated_memories.json')['records']
    assert set(generated)==set(plan['nodes']), 'Hierarchy generation incomplete'
    config = _score_endpoints()
    assert config['answer_model']==config['judge_model']==MODEL
    output = ROOT/('evaluate_'+args.retrieval)
    output.mkdir(exist_ok=True)
    cfg = space_config()
    if args.retrieval=='cloud':
        snapshot = read(ROOT/'verified_cloud_memories.json')
        assert not snapshot['issues'] and set(snapshot['records'])==set(generated), 'Cloud verification incomplete'
        by_id = {n['cloud_id']:n for n in snapshot['records'].values()}
        ranker = None
    else:
        by_id = generated
        ranker = LocalBM25(generated)
    state_path = output/'results.partial.json'
    state = read(state_path) if state_path.exists() else {'run':RUN,'retrieval':args.retrieval,'started_at':now(),
        'model':MODEL,'results':{},'errors':{},'evidence_source':'verified Space summary' if args.retrieval=='cloud' else 'local generated hierarchy',
        'source_l0_used_at_test_time':False,'gold_used_for_retrieval_or_answer':False}
    row_dir = output/'rows'
    row_dir.mkdir(exist_ok=True)
    for path in row_dir.glob('*.json'):
        row = read(path)
        state['results'][row['question_id']] = row
    if (output/'errors.json').exists():
        state['errors'] = read(output/'errors.json')
    protocol = {'retrieval':args.retrieval,'hierarchy_sha256':digest(json.dumps(generated,sort_keys=True)),
        'questions_sha256':hashlib.sha256(QUESTIONS.read_bytes()).hexdigest(),
        'answer_template_sha256':digest(_OPEN_ANSWER),'judge_template_sha256':digest(_ACCURACY),
        'model':MODEL,'temperature':0,'budget_chars':8000,'max_unique_memories':12,
        'cloud_recall_limit':100,'cloud_query':'Conversation {sample_id}. {question}',
        'cross_conversation_filter':True,'raw_L0_evidence':False}
    if (output/'protocol.json').exists():
        assert read(output/'protocol.json')==protocol, 'Protocol changed; use a new output directory'
    else:
        write(output/'protocol.json',protocol)
    lock = asyncio.Lock(); sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=300,trust_env=False) as client:
        if args.retrieval=='cloud':
            balance = unwrap(await client.get(cfg['SPACE']+'/credits/balance',headers={'X-API-Key':cfg['KEY']}))['balance_units']
            pending = sum(q['question_id'] not in state['results'] for q in questions)
            assert balance>=pending*100, 'Space credits insufficient for all remaining recalls'
        checkpoints = 0
        async def checkpoint(qid):
            nonlocal checkpoints
            async with lock:
                if qid in state['results']:
                    write(row_dir/(qid.replace('#','_')+'.json'),state['results'][qid])
                write(output/'errors.json',state['errors'])
                checkpoints += 1
                if checkpoints%60==0:
                    write(state_path,state)
                    report(state,output)
        async def chat(prompt, role):
            for attempt in range(4):
                try:
                    r = await client.post(config[role+'_base'].rstrip('/')+'/chat/completions',
                        headers={'Authorization':'Bearer '+config[role+'_key']},
                        json={'model':MODEL,'temperature':0,'messages':[{'role':'user','content':prompt}]})
                    r.raise_for_status(); body = r.json()
                    text = str(body['choices'][0]['message']['content']).strip()
                    if not text: raise ValueError('empty model response')
                    return text, {'returned_model':body.get('model'),'response_id':body.get('id'),
                                  'usage':body.get('usage'),'prompt_sha256':digest(prompt)}
                except Exception:
                    if attempt==3:raise
                    await asyncio.sleep(2**attempt)
        async def one(q):
            qid = q['question_id']; conv = q['sample_id']
            if 'correct' in state['results'].get(qid,{}):return
            async with sem:
                try:
                    row = state['results'].get(qid)
                    if row is None:
                        if ranker is None:
                            req = {'query':f"Conversation {conv}. {q['question']}",'limit':100,
                                   'idempotency_key':str(uuid.uuid5(uuid.NAMESPACE_URL,RUN+'/recall/'+qid))}
                            # No automatic recall retry with a new billing identity.
                            response = await client.post(cfg['SPACE']+'/memories/recall',
                                headers={'X-API-Key':cfg['KEY']},json=req)
                            data = unwrap(response)
                            write(output/(qid.replace('#','_')+'.recall.json'),data)
                            raw = data['memories']
                            hits = [h for h in raw if h['id'] in by_id and by_id[h['id']]['conv']==conv]
                            excluded = len(raw)-len(hits)
                        else:
                            hits = ranker.search(conv,q['question']);excluded=0
                        memories,selected = pack(hits,by_id)
                        row = {'question_id':qid,'category':str(q['category']),'question':q['question'],
                            'selected':selected,'excluded_hits':excluded,'evidence':memories,'created_at':now()}
                        state['results'][qid] = row
                        await checkpoint(qid)
                    if 'answer' not in row:
                        prompt = _OPEN_ANSWER.format(memories=row['evidence'],question=q['question'])
                        row['answer'],row['answer_metadata'] = await chat(prompt,'answer')
                        await checkpoint(qid)
                    prompt = _ACCURACY.format(question=q['question'],gold_answer=q['gold'],generated_answer=row['answer'])
                    row['judge'],row['judge_metadata'] = await chat(prompt,'judge')
                    row['correct'] = parse_correct_label(row['judge'])
                    row['gold'] = q['gold'];row['finished_at'] = now()
                    state['errors'].pop(qid,None)
                    await checkpoint(qid)
                    done = sum('correct' in x for x in state['results'].values())
                    if done%20==0:print('scored',done,'/861',flush=True)
                except Exception as exc:
                    state['errors'][qid] = type(exc).__name__+': '+str(exc)
                    await checkpoint(qid)
        selected_questions = questions[:args.limit] if args.limit else questions
        await asyncio.gather(*(one(q) for q in selected_questions))
    write(state_path,state)
    metrics = report(state,output)
    print(json.dumps({'completed':metrics['completed'],'accuracy':metrics['accuracy'],
                      'errors':len(metrics['errors'])}),flush=True)
    if metrics['errors']:raise RuntimeError('Some requests failed; inspect errors and resume')

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--retrieval',choices=['cloud','offline'],required=True)
    p.add_argument('--concurrency',type=int,default=12)
    p.add_argument('--limit',type=int,default=0)
    asyncio.run(main(p.parse_args()))
