"""Artifact and candidate-set audit only; no imports with model/network side effects."""
import ast
import collections
import hashlib
import json
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OLD = ROOT.parent/'locomo_hierarchy_grounded_20261024'
R1 = ROOT.parent/'locomo_hierarchy_relevance_r1_20261024'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    store = read(OLD/'store.json')
    routes = read(R1/'routes_r1.json')['routes']
    qs = {q['question_id']:q for q in read(ROOT.parent/'locomo_temporal_week_20261023/frozen/questions.json')['questions']}
    qids = read(R1/'dev_sample.json')['question_ids']
    # Reuse the exact standalone text-normalization definitions, without
    # importing the retrieval module and its evaluation dependencies.
    parsed = ast.parse((OLD/'grounded.py').read_text(encoding='utf-8'))
    nodes = [n for n in parsed.body if (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='STOP' for t in n.targets))
        or (isinstance(n,ast.FunctionDef) and n.name in ['clean_body','words','compositional_query'])]
    env = {'re':re}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'grounded_normalizers','exec'),env)
    counts = collections.Counter()
    base_windows, novel_counts, rows = [], [], []
    for qid in qids:
        filename = qid.replace('#','_')+'.json'
        values = {a:read(ROOT/'requests/p1'/a/filename) for a in ['B','H_raw','H_fold','L_rel','H_rel']}
        b = values['B']
        base_windows.append(len(b['windows']))
        counts['questions'] += 1
        counts['B_matches_original_lower_evidence'] += b['evidence']==read(OLD/'requests/lower_only'/filename)['evidence']
        for arm in ['H_raw','H_fold','L_rel','H_rel']:
            counts[arm+'_changed_from_B'] += values[arm]['evidence'] != b['evidence']
        counts['H_fold_identical_to_H_rel'] += values['H_fold']['evidence']==values['H_rel']['evidence']
        used = {f for w in b['windows'] for f in w}
        query = qs[qid]['question']
        qt = env['words'](query)
        ranked = []
        for route in routes.values():
            if route['conv'] != qs[qid]['sample_id']:
                continue
            terms = set()
            for alias in route['aliases'][:3]:
                terms |= env['words'](alias)
            overlap = len(terms & qt)
            if overlap:
                ranked.append((overlap,route))
        ranked.sort(key=lambda t:(-t[0],t[1]['route_id']))
        all_targets = {f for _,route in ranked[:200] for f in route['target_fact_ids']}
        unseen = all_targets-used
        novel_counts.append(len(unseen))
        counts['queries_with_unseen_top200_upper_target'] += bool(unseen)
        chosen16 = [route for _,route in ranked[:16]]
        screen_targets = {f for route in chosen16 for f in route['target_fact_ids'] if f not in used}
        counts['top16_pretruncation_loses_unseen_candidates'] += bool(unseen-screen_targets)
        broad_guard = not env['compositional_query'](query)
        direct_time = bool(re.match(r'\s*(?:when\b|(?:what|which)\s+(?:date|day|month|year|week)\b|how\s+long\b)',query,re.I))
        counts['legacy_noncompositional_guard'] += broad_guard
        counts['direct_time_regex_guard'] += direct_time
        rows.append({'question_id':qid,'B_windows':len(b['windows']),'B_fact_ids':len(used),
            'top200_upper_targets':len(all_targets),'top200_upper_targets_outside_B':len(unseen),
            'top16_upper_targets_outside_B':len(screen_targets),
            'guard_legacy_noncompositional':broad_guard,'guard_direct_time':direct_time})
    model_source=(ROOT/'tools/p1_model_screen.py').read_text(encoding='utf-8')
    tree=ast.parse(model_source)
    posts=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='post']
    result={'purpose':'read-only audit; no new model calls or answer scores',
        'counts':dict(counts),'B_window_counts':dict(sorted(collections.Counter(base_windows).items())),
        'top200_upper_targets_outside_B':{'min':min(novel_counts),'median':statistics.median(novel_counts),'max':max(novel_counts),
            'note':'Unique linked target IDs, not proven relevant or answer supporting.'},
        'model_screen_artifact_exists':(ROOT/'P1_MODEL_SCREEN.json').exists(),
        'post_call_keyword_arguments':[[kw.arg for kw in n.keywords] for n in posts],
        'model_screen_has_auth_headers_keyword':any(any(k.arg=='headers' for k in n.keywords) for n in posts),
        'new_answer_scores':None,'per_question':rows,
        'input_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'HANDOFF_TO_GPT_20261024.md',ROOT/'P1_SELECTOR_SUMMARY.json',ROOT/'P1_FACT_LOSS.json',ROOT/'P0_EVIDENCE_AUDIT.json',ROOT/'tools/p1_model_screen.py',ROOT/'tools/p1_groups.py']}}
    (ROOT/'CODEX_HANDOFF_AUDIT_20261024.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ['per_question','input_hashes']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
