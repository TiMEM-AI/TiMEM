"""Read-only replay of R1 development selection; no API calls or new scoring."""
import collections
import hashlib
import importlib.util
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / 'locomo_hierarchy_grounded_20261024'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    spec = importlib.util.spec_from_file_location('r1_review_pipeline', ROOT/'tools/r1_pipeline.py')
    pipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipe)
    store, routes, events, questions = pipe.load_inputs()
    facts = store['facts']
    by_group = collections.defaultdict(list)
    group_of = {}
    for fid, fact in facts.items():
        group_of[fid] = fact.get('context_group') or 'solo:'+fid
        by_group[group_of[fid]].append(fid)
    conn = sqlite3.connect('file:'+(SOURCE/'index.sqlite').as_posix()+'?mode=ro', uri=True)
    results = read(ROOT/'eval/dev/results.partial.json')['results']
    qids = read(ROOT/'dev_sample.json')['question_ids']
    stats, categories = collections.Counter(), collections.defaultdict(lambda: collections.Counter())
    accepted_total = collections.Counter()
    cases, per_question = [], []
    for qid in qids:
        q = questions[qid]
        payloads = {a:read(ROOT/'requests/dev'/a/(qid.replace('#','_')+'.json')) for a in ['L','H']}
        scores = pipe.fact_scores(conn, q['sample_id'], q['question'])
        window_facts = collections.defaultdict(list)
        window_score = {}
        for fid, score in scores:
            group = group_of[fid]
            window_facts[group].append(fid)
            window_score[group] = max(window_score.get(group, 0), score)
        base = sorted(window_score, key=lambda g:-window_score[g])[:pipe.MAX_WINDOWS]
        in_base = {fid for g in base for fid in window_facts[g]}
        selected = []
        for route, score in pipe.route_scores(routes, q['sample_id'], q['question']):
            added = [fid for fid in route['target_fact_ids'] if fid not in in_base]
            if not added or any(fid not in facts for fid in route['target_fact_ids']):
                continue
            gs = {group_of[fid] for fid in added}
            if gs:
                selected.append((route, gs, added))
            if len(selected) >= pipe.UPPER_SLOTS:
                break
        accepted = selected[:min(4, len(base[pipe.PROTECTED:]))]
        replaced = list(reversed(base[pipe.PROTECTED:]))[:len(accepted)]
        chosen = list(dict.fromkeys(base[:pipe.PROTECTED]
            + [g for _, gs, _ in accepted for g in sorted(gs)]
            + [g for g in base[pipe.PROTECTED:] if g not in replaced]))[:pipe.MAX_WINDOWS]
        def rendered_ids(groups):
            return [fid for g in groups for fid in sorted(window_facts.get(g) or by_group[g],
                key=lambda fid:facts[fid].get('observed_at') or '')[:7]]
        low_ids, high_ids = rendered_ids(base), rendered_ids(chosen)
        accepted_added = set()
        for route, gs, added in accepted:
            accepted_total['routes'] += 1
            accepted_total['only_existing_B_groups'] += gs.issubset(set(base))
            accepted_total['no_new_target_rendered'] += not (set(added)&set(high_ids))
            accepted_total['no_new_target_rendered_outside_L'] += not ((set(added)&set(high_ids))-set(low_ids))
            accepted_added.update(added)
        changed = payloads['L']['evidence'] != payloads['H']['evidence']
        has_upper = payloads['H']['audit']['has_upper_window']
        stats['questions'] += 1
        stats['evidence_bytes_changed'] += changed
        stats['has_upper_window_flag'] += has_upper
        stats['changed_without_upper_window_flag'] += changed and not has_upper
        stats['upper_accepted_questions'] += bool(accepted)
        stats['window_count_reduced_questions'] += len(chosen)<len(base)
        stats['window_count_reduction'] += len(base)-len(chosen)
        stats['L_windows'] += len(base)
        stats['H_windows'] += len(chosen)
        stats['duplicate_fact_ids_L'] += len(low_ids)-len(set(low_ids))
        stats['duplicate_fact_ids_H'] += len(high_ids)-len(set(high_ids))
        same_evidence_different_verdict = not changed and results[qid+':L']['correct'] != results[qid+':H']['correct']
        stats['same_evidence_different_verdict'] += same_evidence_different_verdict
        direct_time = bool(re.match(r'\s*(?:when\b|(?:what|which)\s+(?:date|day|month|year|week)\b|how\s+long\b)',q['question'],re.I))
        stats['direct_time_queries'] += direct_time
        stats['direct_time_queries_changed'] += direct_time and changed
        cat = str(q['category'])
        categories[cat]['total'] += 1
        categories[cat]['changed'] += changed
        categories[cat]['L_correct'] += int(results[qid+':L']['correct'])
        categories[cat]['H_correct'] += int(results[qid+':H']['correct'])
        missing = sorted(accepted_added-set(high_ids))
        row = {'qid':qid, 'category':cat, 'question':q['question'], 'evidence_changed':changed,
            'has_upper_window':has_upper, 'base_windows':len(base), 'final_windows':len(chosen),
            'accepted_routes':len(accepted), 'replaced_windows':replaced,
            'added_final_fact_ids':sorted(set(high_ids)-set(low_ids)),
            'removed_final_fact_ids':sorted(set(low_ids)-set(high_ids)),
            'accepted_added_targets_missing_in_final':missing,
            'L_correct':results[qid+':L']['correct'], 'H_correct':results[qid+':H']['correct']}
        per_question.append(row)
        if row['L_correct'] != row['H_correct']:
            cases.append({**row,'gold':q['gold'], 'L_answer':results[qid+':L']['generated'],
                'H_answer':results[qid+':H']['generated']})
        for arm, groups in [('L',base),('H',chosen)]:
            # Existing audit tokens are far below the cap. Recreate text directly
            # so this read-only review needs no tokenizer data download.
            assert payloads[arm]['audit']['evidence_tokens'] < pipe.TOKEN_BUDGET
            blocks = []
            for group in groups:
                ids = rendered_ids([group])
                blocks.append('\n'.join('- ['+str(facts[fid].get('observed_at'))[:19]+'] '+facts[fid].get('text','') for fid in ids))
            replay = '\n'.join(blocks) if blocks else '(no memories)'
            assert replay == payloads[arm]['evidence'], (qid,arm,'replay mismatch')
    conn.close()
    route_aliases = collections.Counter(len(r['aliases']) for r in routes.values())
    report = {'scope':'Read-only deterministic replay of existing requests; no model calls and no changed scores',
        'stats':dict(stats),'accepted_route_diagnostics':dict(accepted_total),
        'categories':{k:dict(v) for k,v in categories.items()},
        'routes_with_more_than_four_aliases':sum(n for k,n in route_aliases.items() if k>4),
        'aliases_ignored_by_first_four_cap':sum((k-4)*n for k,n in route_aliases.items() if k>4),
        'paired_disagreements':cases, 'per_question':per_question,
        'hashes':{str(p):digest(p) for p in [ROOT/'tools/r1_pipeline.py', ROOT/'tools/r1_eval.py',
            ROOT/'requests/dev/protocol.json',ROOT/'eval/dev/results.partial.json']},
        'all_saved_evidence_reproduced':True,
        'limits':'Missing rendered targets prove a selection/rendering mismatch, not whether a target is an answer-supporting fact. Source scores are unchanged.'}
    (ROOT/'CODEX_DEV_REVIEW_20261024.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['per_question','hashes']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
