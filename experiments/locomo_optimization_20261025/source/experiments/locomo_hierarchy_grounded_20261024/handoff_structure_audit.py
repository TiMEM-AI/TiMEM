"""Read-only structural diagnostics for the next-round handoff; no model calls."""
import collections
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLAN = ROOT.parent / 'locomo_space_l1_l5_20261024' / 'hierarchy_plan.json'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    plan, store = read(PLAN), read(ROOT / 'store.json')
    levels = collections.defaultdict(set)
    for route in store['routes'].values():
        for link in route['links']:
            levels[link['fact_id']].add(route['level'])
    node_counts = {}
    for level in ['L1', 'L2', 'L3', 'L4', 'L5']:
        nodes = [n for n in plan['nodes'].values() if n['level'] == level]
        node_counts[level] = {'total': len(nodes), 'single_child': sum(len(n['child_ids']) == 1 for n in nodes)}
    full = read(ROOT / 'full' / 'paired_comparison.json')['full_861']
    confirm = read(ROOT / 'confirm' / 'analysis.json')['comparison']
    result = {
        'purpose': 'handoff-only structural audit; no new evaluation',
        'inputs_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [PLAN, ROOT / 'store.json']},
        'node_counts': node_counts,
        'route_units': len(store['routes']),
        'route_links': sum(len(r['links']) for r in store['routes'].values()),
        'facts_linked_by_number_of_upper_levels': dict(sorted(collections.Counter(len(v) for v in levels.values()).items())),
        'facts_with_any_upper_link': len(levels),
        'common_fixed': sorted(set(full['fixed']) & set(confirm['fixed'])),
        'common_regressed': sorted(set(full['regressed']) & set(confirm['regressed'])),
        'fixed_to_regressed': sorted(set(full['fixed']) & set(confirm['regressed'])),
        'regressed_to_fixed': sorted(set(full['regressed']) & set(confirm['fixed'])),
        'limits': 'Structural overlap is not proof of causal ranking harm; shared outcome changes are not proof of retrieval errors.'
    }
    path = ROOT / 'HANDOFF_STRUCTURE_AUDIT_20261024.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
