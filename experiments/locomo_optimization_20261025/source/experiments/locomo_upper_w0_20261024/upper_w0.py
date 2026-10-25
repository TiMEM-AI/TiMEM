"""W0-compatible upper-memory storage and evidence rendering.

Only prepare_store reads frozen source turns, to persist verified annotations.
Retrieval and evaluation read the stored hierarchy; they never query L0.
"""
import collections, dataclasses, functools, hashlib, importlib.util, json, re, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREVIOUS = ROOT.parent/'locomo_space_l1_l5_20261024'
W0 = ROOT.parent/'locomo_temporal_week_20261023'
sys.path.insert(0,str(PREVIOUS))
sys.path.insert(0,str(W0/'arms'))
from build_hierarchy import read, write, now, BENCH, MODEL, _score_endpoints
from run_full import LocalBM25, pack as old_pack, EXPECTED, NAMES, BASELINE
import arm_w0
from evaluators.aml_score import format_memories, _OPEN_ANSWER, _ACCURACY, ANSWER_INPUT_TOKEN_BUDGET

a0 = arm_w0.arm_b.a0
week = arm_w0.week_ext
UPPER = {'L3','L4','L5'}
SCHEMA = 'upper-w0-fact-time-v2'
HEADER = a0.ANNOTATION_HEADER

def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def norm(value):
    return re.sub(r'\s+',' ',value).strip().lower()

def eligible_phrases(text, kind):
    """Recognize whole, longest phrases before matching verified source spans."""
    patterns = a0._SUPPORTED if kind=='calendar' else week._WEEK_PHRASES
    blocked=[]
    if kind=='week':
        blocked=[m.span() for pattern in week._BLOCKERS for m in pattern.finditer(text)]
    candidates=[]
    for _,pattern in patterns:
        candidates.extend(m for m in pattern.finditer(text))
    # 'day before yesterday' consumes its shorter 'yesterday' occurrence.
    candidates.sort(key=lambda m:-(m.end()-m.start()))
    occupied=[];out=set()
    for match in candidates:
        if any(match.start()<end and match.end()>start for start,end in blocked+occupied):continue
        occupied.append(match.span());out.add(norm(match.group(0)))
    return out

def split_units(node, observations):
    units = []
    for index,line in enumerate(node['content'].splitlines()):
        if not line.strip() or line.startswith('Observation window:'):
            continue
        # These are headings, not factual observations.
        if line in ['- **Monthly Profile**','- **Event Timeline**']:
            continue
        brackets = re.findall(r'\[([^\]]+)\]',line)
        observed = None
        for bracket in brackets:
            candidates = [x for x in observations if bracket==x or bracket==x[:10]]
            if len(candidates)==1:
                observed = candidates[0];break
        text = re.sub(r'^\s*[-*]\s+','',line)
        if line.startswith(('- ','* ')):
            uid = 'fact:'+sha(node['id']+'/'+str(index)+'/'+text)[:32]
            units.append({'id':uid,'text':text,'observed_at':observed,'annotations':[],
                          'date_policy':'own observation marker; never parent window'})
        elif units:
            units[-1]['text']+='\n'+line
    return units

def prepare_store():
    previous = read(PREVIOUS/'generated_memories.json')
    old = previous['records']
    plan = read(PREVIOUS/'hierarchy_plan.json')
    db = sqlite3.connect('file:'+Path(plan['source_path']).as_posix()+'?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    source = {r['id']:dict(r) for r in db.execute('SELECT * FROM source_turns')}
    db.close()
    @functools.lru_cache(None)
    def descendants(nid):
        node = old[nid]
        return tuple(dict.fromkeys(node['source_ids']+[s for c in node['child_ids'] for s in descendants(c)]))
    parsed = {}
    for sid,turn in source.items():
        value = a0.SourceTurn(sid,turn['user_id'],turn['role'],turn['content'],turn['observed_at'])
        annotations = []
        for mention in a0.parse_time_mentions(value):
            if mention.status=='resolved' and mention.normalized_value:
                annotations.append({'kind':'calendar','data':dataclasses.asdict(mention),
                                    'line':a0._annotation_line(mention)})
        for mention in week.parse_week_mentions({'source_id':sid,**turn},with_range=False):
            annotations.append({'kind':'week','data':dataclasses.asdict(mention),'line':week._line(mention)})
        parsed[sid] = annotations
    records = {}
    issues = [];stats = collections.Counter()
    for nid,node in old.items():
        if node['level'] not in UPPER:
            records[nid]=node
            continue
        sids = descendants(nid)
        observations = sorted({source[s]['observed_at'] for s in sids})
        by_date = collections.defaultdict(list)
        for sid in sids:
            assert source[sid]['user_id'].split(':')[-1]==node['conv']
            for entry in parsed[sid]:by_date[source[sid]['observed_at']].append(entry)
        units = split_units(node,observations)
        for unit in units:
            stats['facts']+=1
            if not unit['observed_at']:
                issues.append({'node':nid,'unit':unit['id'],'reason':'unverified observation marker'})
                continue
            seen = set()
            allowed={kind:eligible_phrases(unit['text'],kind) for kind in ['calendar','week']}
            for entry in by_date[unit['observed_at']]:
                data = entry['data'];phrase=data['raw_phrase']
                if norm(phrase) not in allowed[entry['kind']]:continue
                if re.search(r'\b(?:about|around|roughly|approximately|early|late|mid|sometime)\s+'+re.escape(phrase),unit['text'],re.I):
                    continue
                key = (norm(phrase),data.get('normalized_value',data.get('anchored_value')),data['precision'],data.get('rule_id',data.get('rule')))
                if key in seen:continue
                seen.add(key)
                # Check the exact original span before persisting the annotation.
                sid = data['source_id'];start,end=data['span']
                assert source[sid]['content'][start:end]==phrase
                assert data['observed_at']==unit['observed_at']
                value = {**entry,'fact_id':unit['id'],'source_span_verified':True,
                    'binding':'same observation time and verbatim relative phrase; no claim of unique event alignment'}
                unit['annotations'].append(value);stats[entry['kind']+'_bindings']+=1
        content = 'Derived memory summary:\n'+'\n'.join(u['text'] for u in units)
        records[nid] = {**node,'content':content,'index_text_sha256':sha(node['content']),
            'storage_schema':SCHEMA,'window':{'start':node['start'],'end':node['end'],'purpose':'aggregation only'},
            'fact_units':units,'origin_source_ids':list(sids),
            'legacy_content_sha256':sha(node['content'])}
    lower_same = sum(canonical(records[k])==canonical(v) for k,v in old.items() if v['level'] not in UPPER)
    assert lower_same==671
    for lev in UPPER:assert sum(n['level']==lev for n in records.values())==sum(n['level']==lev for n in old.values())
    audit = {'created_at':now(),'schema':SCHEMA,'records':len(records),'changed_upper_nodes':565,
        'unchanged_L1_L2_nodes':lower_same,'stats':dict(stats),'issues':issues,
        'source_sha256':hashlib.sha256(Path(plan['source_path']).read_bytes()).hexdigest(),
        'memory_construction_reads_questions_or_gold':False,'original_text_copied_as_new_facts':False,
        'whole_phrase_and_longest_match_validation':True,
        'source_use':'construction-time verification of inherited short time phrases only',
        'passed':not issues}
    write(ROOT/'storage_audit.json',audit)
    write(ROOT/'memories.json',{'schema':SCHEMA,'created_at':now(),'records':records})
    if issues:raise RuntimeError(f'{len(issues)} upper facts lack verified observation markers')
    print('STORE',json.dumps(audit,ensure_ascii=False),flush=True)
    return records

def selected_nodes(hits, old):
    seen = set();selected=[]
    for hit in hits:
        key = re.sub(r'\s+','',old[hit['id']]['content'])
        if key in seen:continue
        seen.add(key);selected.append(hit)
        if len(selected)==12:break
    return selected

def render(hits, old, records, query, *, changed=True):
    rows=[];selected=[];seen_facts=set()
    for hit in selected_nodes(hits,old):
        nid=hit['id'];node=records[nid]
        selected.append({'id':nid,'level':node['level'],'conv':node['conv'],'score':hit['score']})
        if not changed or node['level'] not in UPPER:
            rows.append({'id':nid,'content':old[nid]['content'],'created_at':node['start'],
                         'level':node['level'],'annotations':[]})
            continue
        grouped=collections.OrderedDict()
        for unit in node['fact_units']:
            key=(unit['observed_at'],norm(unit['text']))
            if key in seen_facts:continue
            seen_facts.add(key)
            group=grouped.setdefault(unit['observed_at'],{'units':[],'annotations':[]})
            group['units'].append(unit['text']);group['annotations'].extend(unit['annotations'])
        for observed,group in grouped.items():
            rows.append({'id':nid+':'+str(observed),'content':'Derived memory summary:\n'+'\n'.join(group['units']),
                'created_at':observed,'level':node['level'],'annotations':group['annotations']})
    is_date = a0.plan_date_query(query) is not None
    c1=[];wk=[];seen=set()
    if changed and is_date:
        for index,row in enumerate(rows):
            for entry in row['annotations']:
                key=(entry['data']['source_id'],tuple(entry['data']['span']),entry['kind'])
                if key in seen:continue
                seen.add(key)
                (c1 if entry['kind']=='calendar' else wk).append((index,entry))
    # Same C1 then W0 all-or-nothing annotation budget semantics as frozen W0.
    appended=0;rendered=[];skips=[]
    for kind,entries in [('calendar',c1),('week',wk)]:
        per_row=collections.defaultdict(list)
        for index,entry in entries:per_row[index].append(entry)
        required=sum(sum(len(e['line'])+1 for e in values)+(len(HEADER)+2 if HEADER not in rows[index]['content'] else 0)
                     for index,values in per_row.items())
        if appended+required>8000:
            skips.append({'kind':kind,'reason':'annotation_budget_exceeded','annotations':len(entries)})
            continue
        for index,values in per_row.items():
            rows[index]['content']+= ('\n' if HEADER in rows[index]['content'] else '\n\n'+HEADER+'\n')+'\n'.join(e['line'] for e in values)
            rendered.extend(e for e in values)
        appended+=required
    evidence=format_memories(rows,question=query,options=None)
    stats={'formatter':'bench.evaluators.aml_score.format_memories','input_token_budget':ANSWER_INPUT_TOKEN_BUDGET,
        'annotation_budget_chars':8000,'annotation_added_chars':appended,'is_date_query':is_date,
        'annotations_rendered':len(rendered),'annotation_skips':skips,'evidence_chars':len(evidence),
        'evidence_rows':len(rows),'parent_window_used_as_upper_fact_anchor':False,
        'rendered_annotation_trace':rendered,'evidence_row_times':[
            {'id':r['id'],'level':r['level'],'observed_at':r['created_at']} for r in rows]}
    return evidence,selected,stats

def prepare_requests():
    # Memory construction is already complete before questions are loaded.
    records=read(ROOT/'memories.json')['records']
    old=read(PREVIOUS/'generated_memories.json')['records']
    questions=read(W0/'frozen/questions.json')['questions']
    old_rows=read(PREVIOUS/'evaluate_offline/results.partial.json')['results']
    ranker=LocalBM25(old)
    frozen={};mismatches=[];stats=collections.Counter();proof=[]
    for q in questions:
        qid=q['question_id'];hits=ranker.search(q['sample_id'],q['question']);frozen[qid]=hits
        old_evidence,_=old_pack(hits,old)
        if old_evidence!=old_rows[qid]['evidence']:mismatches.append(qid)
        for arm in ['budget_control','upper_w0']:
            evidence,selected,details=render(hits,old,records,q['question'],changed=arm=='upper_w0')
            assert all(s['conv']==q['sample_id'] for s in selected)
            assert details['annotation_added_chars']<=8000
            assert not details['parent_window_used_as_upper_fact_anchor']
            folder=ROOT/'requests'/arm;folder.mkdir(parents=True,exist_ok=True)
            write(folder/(qid.replace('#','_')+'.json'),{'question_id':qid,'evidence':evidence,'selected':selected,'diagnostics':details,
                'answer_prompt_sha256':sha(_OPEN_ANSWER.format(memories=evidence,question=q['question']))})
            if arm=='upper_w0':
                stats['queries']+=1;stats['rendered_annotations']+=details['annotations_rendered']
                stats['over_8000_chars']+=len(evidence)>8000
                stats['annotation_budget_drop_queries']+=bool(details['annotation_skips'])
                stats['date_queries']+=details['is_date_query']
                if details['annotations_rendered'] and len(proof)<4:
                    proof.append({'qid':qid,'evidence_prefix':evidence[:1500],'annotation':details['rendered_annotation_trace'][0]})
    write(ROOT/'frozen_retrieval.json',frozen)
    write(ROOT/'format_examples.json',proof)
    audit={'created_at':now(),'queries':861,'previous_evidence_reproduction_mismatches':mismatches,
        'ranking_index':'unchanged previous hierarchy BM25 text, parameters and conversation isolation',
        'formatter_function_sha256':sha(__import__('inspect').getsource(format_memories)),
        'answer_template_sha256':sha(_OPEN_ANSWER),'judge_template_sha256':sha(_ACCURACY),
        'stats':dict(stats),'passed':not mismatches,
        'parity':'W0 formatter, independent calendar block, per-fact observation date and precision; derived text remains explicitly derived',
        'not_identical':'Memory text differs from W0 raw conversations; top-12 node selection inherited from the preceding offline experiment.'}
    write(ROOT/'format_audit.json',audit)
    assert not mismatches,'Previous retrieval changed; inspect format audit before scoring'
    print('FORMAT',json.dumps(audit,ensure_ascii=False),flush=True)

if __name__=='__main__':
    ROOT.mkdir(exist_ok=True)
    prepare_store()
    prepare_requests()
