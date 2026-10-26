"""Offline, read-only reconstruction of submitted Stage B calls; no client construction."""
from __future__ import annotations
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("r13_stage", ROOT / "stage_b.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
sha = lambda v: hashlib.sha256(v.encode()).hexdigest()
def offline_format(records, *, question, options):
    # UTF-8 byte count upper-bounds cl100k tokens. If the entire input fits this
    # stricter bound, the original formatter necessarily keeps the same lines.
    lines=[]
    for rec in records:
        content=str(rec.get('content') or '').strip()
        if not content:continue
        at=str(rec.get('created_at') or '').strip()
        lines.append(f'- [{at}] {content}' if at else f'- {content}')
    assert len(question.encode())+sum(len(line.encode()) for line in lines)<117760
    return '\n'.join(lines) if lines else '(no memories)'
s.g.format_memories=offline_format
questions = {q['question_id']: q for q in json.loads(s.QUESTIONS.read_text(encoding='utf-8'))['questions']}
dev = json.loads((s.R1/'dev_sample.json').read_text(encoding='utf-8'))['question_ids']
folded = json.loads((s.R1/'routes_r1.json').read_text(encoding='utf-8'))['routes']
facts = json.loads((s.GROUNDED/'store.json').read_text(encoding='utf-8'))['facts']
rng = random.Random(s.SEED)
picked=[]
for cat in ('2','4','1','3'):
    pool=sorted(qid for qid in dev if str(questions[qid].get('category'))==cat)
    picked += rng.sample(pool,min(5,len(pool)))
retriever=s.g.Retriever()
prompts={}; specs={}; overview=[]
for qid in picked[:20]:
    q=questions[qid]; base,_=retriever.windows(q['sample_id'],q['question'],False)
    evidence,_=retriever.render(base,q['question'])
    pools={a:s.build_pool(q['sample_id'],q['question'],base,facts,folded,a) for a in ('L_rel','H_rel')}
    basef={fid for w in base for fid in w['fact_ids']}
    lower_all={f['id'] for f in facts.values() if f['conv']==q['sample_id'] and f['id'] not in basef and s.g.words(q['question']) & s.g.words(f['text'])}
    overview.append({'qid':qid,'category':str(q['category']),'base_windows':len(base),
        'base_fact_count':len(basef),'lower_pool':len(pools['L_rel']),'upper_pool_total':len(pools['H_rel']),
        'upper_unique_over_lower':len({x['id'] for x in pools['H_rel']}-{x['id'] for x in pools['L_rel']}),
        'upper_added_overlap_query_token':sum(bool(s.g.words(q['question'])&s.g.words(x['text'])) for x in pools['H_rel'][8:]),
        'lower_eligible_all_count':len(lower_all),
        'actual_upper_added_ids':[x['id'] for x in pools['H_rel'][8:]]})
    for arm,pool in pools.items():
        if not pool: continue
        protected=[f"w{i+1}: {w['fact_ids'][0]}" for i,w in enumerate(base[:20])]
        tail=[f"t{i+1}: "+facts[w['fact_ids'][0]]['text'][:160] for i,w in enumerate(base[20:]) if w['fact_ids'][0] in facts]
        cands=[{'id':f'c{i+1}','centre':item['text'],'context':[]} for i,item in enumerate(pool)]
        prompt=s.PROMPT.format(question=q['question'],protected='\n'.join(protected),tail='\n'.join(tail),candidates='\n'.join(f"[{c['id']}] {c['centre']}" for c in cands))
        h=sha(prompt)
        item={'qid':qid,'arm':arm,'category':str(q['category']),'base':base,'base_evidence':evidence,'pool':pool,
            'prompt_sha256':h,'protected_prompt_chars':len('\n'.join(protected)),
            'tail_centres':len(tail),'tail_facts_actual':sum(len(w['fact_ids']) for w in base[20:]),
            'protected_facts_actual':sum(len(w['fact_ids']) for w in base[:20]),
            'tail_centres_over_160':sum(len(facts[w['fact_ids'][0]]['text'])>160 for w in base[20:])}
        prompts[h]=item;specs[(qid,arm)]=item

logs=[json.loads(line) for line in (ROOT/'screen_stageB.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
rows=[];counts=collections.Counter();relevance=collections.Counter();models=collections.Counter()
for i,log in enumerate(logs):
    item=prompts.get(log['prompt_sha256'])
    assert item is not None, (i,log['prompt_sha256'])
    counts['records']+=1;counts[log['status']]+=1
    models[log.get('response_model','<missing>')]+=1
    out={'log_index':i,'qid':item['qid'],'arm':item['arm'],'category':item['category'],'status':log['status'],
        'prompt_hash_reproduced':True,'base_windows':len(item['base']),'candidate_count':len(item['pool']),
        'protected_facts_actual':item['protected_facts_actual'],'tail_facts_actual':item['tail_facts_actual'],
        'tail_centres_over_160':item['tail_centres_over_160']}
    if log['status']=='valid':
        v=log['parsed']; sel=set(v.get('selected_optional_window_ids') or []); rem=set(v.get('removed_base_tail_ids') or [])
        chosen=list(item['base']);removed=[]
        for idx in range(len(chosen)-1,19,-1):
            if f't{idx-19}' in rem: removed.append(chosen.pop(idx))
        for cid in sel:
            by={f'c{j+1}':p for j,p in enumerate(item['pool'])};p=by.get(cid)
            if p:chosen.append({'center':p['id'],'fact_ids':[p['id']],'channel':'stageB'})
        chosen=chosen[:24]
        evidence,diag=retriever.render(chosen,questions[item['qid']]['question'])
        selectedf=[item['pool'][int(cid[1:])-1]['id'] for cid in sel]
        finalf=[f for w in chosen for f in w['fact_ids']]
        decisions=v.get('evidence_decisions') or []
        for d in decisions:
            relevance[d.get('relevance','<missing>')]+=1
            counts['constraint_mismatch']+=bool(d.get('constraint_mismatch'))
        counts['selected']+=len(sel);counts['removed']+=len(rem);counts['changed']+=evidence!=item['base_evidence']
        counts['deletion_only']+=bool(rem) and not sel
        out.update({'selected':sorted(sel),'removed':sorted(rem),'selected_fact_ids':selectedf,
            'selected_retained':sum(x in finalf for x in selectedf),'final_windows':len(chosen),
            'removed_windows_actual':len(removed),'removed_fact_count':sum(len(w['fact_ids']) for w in removed),
            'removed_fact_ids':[f for w in removed for f in w['fact_ids']],
            'changed':evidence!=item['base_evidence'],'evidence_sha256':sha(evidence),'base_sha256':sha(item['base_evidence']),
            'protected_prefix_preserved':chosen[:20]==item['base'][:20],
            'tail_ids_valid':rem<=set(f't{j+1}' for j in range(max(0,len(item['base'])-20))),
            'lost_query_support':v.get('lost_query_support'),'new_query_support':v.get('new_query_support'),
            'decision_ids_complete':{d.get('window_id') for d in decisions}==set(log['candidates']),
            'supporting_fact_ids_refer_to_supplied_real_ids':all(all(f in log['candidates'] for f in d.get('supporting_fact_ids',[])) for d in decisions),
            'render_diagnostics':diag})
    rows.append(out)
out={'schema':'codex-r13-stageB-offline-audit-v1','network_calls':0,'stage_b_summary_exists':(ROOT/'STAGE_B_SUMMARY.json').exists(),
    'formatter_equivalence':'Full text has <117760 UTF-8 bytes incl question in every render; byte count upper-bounds cl100k tokens, so no formatter truncation occurs.',
    'planned_questions':len(picked[:20]),'planned_calls':40,'counts':dict(counts),'relevance':dict(relevance),
    'response_models':dict(models),'distinct_questions_logged':len({r['qid'] for r in rows}),
    'complete_valid_pairs':sum(all(any(r['qid']==qid and r['arm']==arm and r['status']=='valid' for r in rows) for arm in ('L_rel','H_rel')) for qid in picked[:20]),
    'remaining_valid_calls':40-sum(r['status']=='valid' for r in rows),'rows':rows,'planned_pool_audit':overview,
    'inputs_sha256':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ('stage_b.py','screen_stageB.jsonl')}}
(ROOT/'CODEX_STAGE_B_OFFLINE_AUDIT.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in out.items() if k not in ('rows','planned_pool_audit','inputs_sha256')},ensure_ascii=False,indent=2))
print(json.dumps([{k:v for k,v in row.items() if k in ('qid','arm','category','status','selected','removed','final_windows','removed_fact_count','decision_ids_complete','tail_centres_over_160')} for row in rows],ensure_ascii=False,indent=2))
print(json.dumps({'planned_upper_slots':sum(len(x['actual_upper_added_ids']) for x in overview),
 'planned_upper_slots_overlapping_query_tokens':sum(x['upper_added_overlap_query_token'] for x in overview),
 'actual_removed_facts':sum(x.get('removed_fact_count',0) for x in rows),
 'all_selected_originate_shared_lower_first_eight':all(all(int(cid[1:])<=8 for cid in row.get('selected',[])) for row in rows),
 'planned_pool_rows':overview},ensure_ascii=False,indent=2))
