"""Rejudge all frozen W0 answers with GLM, without regenerating answers."""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BENCH = Path('F:/worktable/research/bench')
SOURCE = Path('F:/codex数据库memory/locomo_temporal_week_20261023')
RESULTS = SOURCE / 'evaluate/all/rep1/results.partial.json'
QUESTIONS = SOURCE / 'frozen/questions.json'
TARGET = 'glm-5.3-flash'
os.environ['NO_PROXY'] = '127.0.0.1,localhost,::1'
os.environ['JUDGE_MODEL'] = TARGET
sys.path.insert(0, str(BENCH))
from runners.aml_run import _score_endpoints, _question_from_row
from evaluators.aml_score import judge_prompt, parse_correct_label
import httpx

def read(p): return json.loads(p.read_text(encoding='utf-8'))
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()
def write(p, value):
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(p)

async def main(args):
    ROOT.mkdir(exist_ok=True)
    endpoints = _score_endpoints()
    assert endpoints['judge_model'] == TARGET
    assert endpoints['judge_key'] and endpoints['judge_base'] == 'https://kspmas.ksyun.com/v1'
    questions = read(QUESTIONS)['questions']
    prior = read(RESULTS)['results']
    answers = {v['question_id']: v for v in prior.values() if v['arm'] == 'w0'}
    assert len(questions) == len(answers) == len({q['question_id'] for q in questions}) == 861
    assert {q['question_id'] for q in questions} == set(answers)
    assert Counter(str(q['category']) for q in questions) == Counter({'2':167,'1':77,'4':581,'3':36})
    config = {'mode':'full W0 fixed-answer cross-model rejudging',
              'questions_path':str(QUESTIONS), 'questions_sha256':digest(QUESTIONS),
              'w0_results_path':str(RESULTS), 'w0_results_sha256':digest(RESULTS),
              'evaluator_sha256':digest(BENCH/'evaluators/aml_score.py'),
              'answer_model':'deepseek-v4.1-flash', 'answer_origin':'frozen prior W0 answers; no new generation',
              'judge_model':TARGET, 'judge_base':endpoints['judge_base'], 'temperature':0, 'n':861}
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    cp = ROOT/'checkpoint.json'
    state = read(cp) if cp.exists() else {'fingerprint':fingerprint,'started_at':now(),'results':{},'errors':{},'http_attempts':0}
    assert state['fingerprint'] == fingerprint, 'resume fingerprint mismatch'
    write(ROOT/'experiment.json', {**config,'fingerprint':fingerprint})
    # Copy evaluation templates for review; no private config is copied.
    if not (ROOT/'aml_score.frozen.py').exists():
        (ROOT/'aml_score.frozen.py').write_bytes((BENCH/'evaluators/aml_score.py').read_bytes())
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(args.concurrency)
    pending = [q for q in questions if q['question_id'] not in state['results']]
    if args.limit: pending = pending[:args.limit]
    print(json.dumps({'pending':len(pending),'already_complete':len(state['results']),
                      'judge_model':TARGET,'answer_model':config['answer_model']}, ensure_ascii=False), flush=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180,connect=30)) as client:
        async def one(q):
            qid=q['question_id']; old=answers[qid]
            prompt=judge_prompt(q['dataset_id'],_question_from_row(q),old['generated'])
            assert prompt
            payload={'model':TARGET,'temperature':0,'messages':[{'role':'user','content':prompt}]}
            request_hash=hashlib.sha256(json.dumps({'base':endpoints['judge_base'],'payload':payload},sort_keys=True).encode()).hexdigest()
            error=None
            async with sem:
                for attempt in range(5):
                    started=time.perf_counter()
                    try:
                        async with lock: state['http_attempts']+=1
                        response=await client.post(endpoints['judge_base'].rstrip('/')+'/chat/completions',
                            headers={'Authorization':'Bearer '+endpoints['judge_key']},json=payload)
                        if response.status_code >= 400:
                            raise RuntimeError('HTTP '+str(response.status_code))
                        body=response.json()
                        raw=str(body['choices'][0]['message'].get('content') or '').strip()
                        correct=parse_correct_label(raw)
                        record={'question_id':qid,'category':str(q['category']), 'question':q['question'],
                            'gold':q['gold'],'generated':old['generated'],
                            'correct':correct,'old_deepseek_correct':bool(old['correct']),
                            'judge_model_requested':TARGET,'judge_model_returned':body.get('model'),
                            'judge_raw':raw,'judge_request_hash':request_hash,
                            'response_id':body.get('id'),'system_fingerprint':body.get('system_fingerprint'),
                            'usage':body.get('usage'),'judged_at':now(),'latency_ms':(time.perf_counter()-started)*1000,
                            'attempts_this_invocation':attempt+1}
                        async with lock:
                            state['results'][qid]=record;state['errors'].pop(qid,None)
                            write(cp,state)
                            n=len(state['results'])
                            if n%10==0 or args.limit or n==861:
                                print(f'judged {n}/861 correct={sum(x["correct"] for x in state["results"].values())}',flush=True)
                        return
                    except Exception as exc:
                        error=type(exc).__name__+': '+str(exc)
                        # Never expose credentials or provider request bodies.
                        if 'HTTP 400' in error or 'HTTP 401' in error or 'HTTP 403' in error or 'HTTP 404' in error:
                            break
                        if attempt<4: await asyncio.sleep(min(2**(attempt+1),16))
                async with lock:
                    state['errors'][qid]={'error':error,'at':now()};write(cp,state)
                    print(f'failed {qid} {error}',flush=True)
        await asyncio.gather(*(one(q) for q in pending))
    rows=[state['results'][q['question_id']] for q in questions if q['question_id'] in state['results']]
    metrics=[]
    for category,label in [('2','时间'),('1','多跳'),('4','单跳'),('3','推理'),('all','总计')]:
        part=rows if category=='all' else [x for x in rows if x['category']==category]
        n=len(part);new=sum(x['correct'] for x in part);old=sum(x['old_deepseek_correct'] for x in part)
        metrics.append({'category':category,'label':label,'n':n,'glm_correct':new,
            'glm_pct':100*new/n if n else None,'old_deepseek_correct':old,
            'old_deepseek_pct':100*old/n if n else None,'delta_pp':100*(new-old)/n if n else None})
    summary={**config,'complete':len(rows)==861,'completed':len(rows),'errors':state['errors'],
        'metrics':metrics,'deepseek_wrong_glm_right':sum(not x['old_deepseek_correct'] and x['correct'] for x in rows),
        'deepseek_right_glm_wrong':sum(x['old_deepseek_correct'] and not x['correct'] for x in rows),
        'returned_models':dict(Counter(str(x['judge_model_returned']) for x in rows)),
        'http_attempts':state['http_attempts'],'finished_at':now()}
    write(ROOT/'summary.json',summary)
    write(ROOT/'aml_score.json',{**summary,'results':rows})
    if rows: write(ROOT/'disagreements.json',{'results':[x for x in rows if x['correct']!=x['old_deepseek_correct']]})
    lines=['# W0 全量固定答案：GLM 重新判分','',
        f'完成：{len(rows)}/861；答题模型 deepseek-v4.1-flash；判分模型 {TARGET}；temperature=0。','',
        '固定已有 W0 答案、题单和评分提示，只更换 judge；不是重新生成答案或重新入库/检索。','',
        '| 类别 | 题数 | 原 DeepSeek 判分 | GLM 判分 | 差值 pp |',
        '|---|---:|---:|---:|---:|']
    for m in metrics:
        if m['n']:
            lines.append(f'| {m["label"]} | {m["n"]} | {m["old_deepseek_correct"]}/{m["n"]} ({m["old_deepseek_pct"]:.2f}%) | {m["glm_correct"]}/{m["n"]} ({m["glm_pct"]:.2f}%) | {m["delta_pp"]:+.2f} |')
    lines+=['','本实验检验评分模型依赖。它不能单独排除答题模型自身能力；要衡量记忆系统贡献，还需在同一答题/判分条件下比较无记忆或打乱记忆对照。',
        '',f'判分错误/未完成：{len(state["errors"])}。实际返回模型：{summary["returned_models"]}。',
        '', '完整逐题答案与 judge 原文见 aml_score.json，分歧见 disagreements.json。']
    (ROOT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'complete':summary['complete'],'completed':len(rows),'metrics':metrics,'errors':len(state['errors'])},ensure_ascii=False),flush=True)
    return 0 if len(rows)==861 or (args.limit and rows and not state['errors']) else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=0);parser.add_argument('--concurrency',type=int,default=4)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
