"""Offline review only: no network, no model calls, no existing artifact writes."""
import ast
import collections
import json
import re
import sys
import types
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import controls
from support_atoms import select_with_support, support_set

def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

f, w = controls.fact, controls.window
pairs = {
    "observed_time_collision": [f("a", "Jolene visited Paris.", obs="2023-05-01T00:00:00+00:00"), f("b", "Jolene visited Paris.", obs="2023-06-01T00:00:00+00:00")],
    "polarity_collision": [f("a", "Jolene recommended Zelda."), f("b", "Jolene never recommended Zelda.")],
    "lowercase_value_collision": [f("a", "Caroline enjoys running."), f("b", "Caroline enjoys swimming.")],
    "role_swap_collision": [f("a", "Jolene recommended Zelda to Deborah."), f("b", "Deborah recommended Zelda to Jolene.")],
}
out = {"scope": "offline reconstruction across all 80 development questions, not the 7 actual model calls",
       "collision_repros": {k: {"same_support": support_set([v[0]]) == support_set([v[1]]), "support_size": len(support_set([v[0]]))} for k, v in pairs.items()}}
out["control_branch_repros"] = {}
for name, facts in [
    ("safe_replacement", [f("f1", "Jolene recommended Zelda."), f("f2", "Jolene recommended Zelda again."), f("f3", "Jolene recommended Overcooked 2.")]),
    ("no_safe_replacement", [f("f1", "Deborah has a husband named Rob."), f("f2", "Rob works as a detective."), f("f3", "Overcooked 2 is a co-op game.")]),
]:
    base = [w("f1", ["f1"]), w("f2", ["f2"])] + [w("x", ["f2"]) for _ in range(20)]
    selected, audit = select_with_support(base, [w("f3", ["f3"])], facts)
    out["control_branch_repros"][name] = {"before": len(base), "after": len(selected), "accepted": len(audit["accepted"]), "replaced": len(audit["replaced"])}
facts = [f("f1", "Jolene recommended Zelda."), f("f4", "Jolene crashed her car.")]
selected, audit = select_with_support([w("f1", ["f1"])], [w("f4", ["f4"])], facts)
out["control_branch_repros"]["different_event_with_capacity"] = {"accepted": len(audit["accepted"]), "rejected": audit["rejected"]}

# Compile only the pure functions and STOP assignment from the inspected files.
# Do not import the scoring/client/runtime modules.
gns = {"re": re}
tree = ast.parse((ROOT / "locomo_hierarchy_grounded_20261024" / "grounded.py").read_text(encoding="utf-8-sig"))
nodes = []
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in {"clean_body", "words"}:
        nodes.append(node)
    if isinstance(node, ast.Assign) and any(isinstance(x, ast.Name) and x.id == "STOP" for x in node.targets):
        nodes.append(node)
exec(compile(ast.Module(body=nodes, type_ignores=[]), "offline_grounded_words", "exec"), gns)
ns = {"g": types.SimpleNamespace(words=gns["words"]), "MAX_CANDIDATES": 40, "UPPER_SLOTS": 16}
tree = ast.parse((HERE / "tools" / "step4_run.py").read_text(encoding="utf-8-sig"))
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_pool"], type_ignores=[]), "offline_build_pool", "exec"), ns)
facts = read(ROOT / "locomo_hierarchy_grounded_20261024" / "store.json")["facts"]
routes = read(ROOT / "locomo_hierarchy_relevance_r1_20261024" / "routes_r1.json")["routes"]
dev = read(ROOT / "locomo_hierarchy_relevance_r1_20261024" / "dev_sample.json")["question_ids"]
questions = {q["question_id"]: q for q in read(ROOT / "locomo_temporal_week_20261023" / "frozen" / "questions.json")["questions"]}
counts = collections.Counter()
rows = []
for qid in dev:
    q = questions[qid]
    baseline = read(ROOT / "locomo_hierarchy_relevance_r1_1_20261024" / "requests" / "p1" / "B" / (qid.replace("#", "_") + ".json"))
    base = [{"fact_ids": ids} for ids in baseline["windows"]]
    lower = ns["build_pool"](q["sample_id"], q["question"], base, facts, routes, "L_rel")
    higher = ns["build_pool"](q["sample_id"], q["question"], base, facts, routes, "H_rel")
    l_ids, h_ids = [x["id"] for x in lower], [x["id"] for x in higher]
    duplicate = len(h_ids) - len(set(h_ids))
    high = sum("levels" in x for x in higher)
    counts.update({"questions": 1, "L_slots": len(lower), "L_unique": len(set(l_ids)), "H_slots": len(higher), "H_unique": len(set(h_ids)), "H_upper_slots": high, "H_lower_slots": len(higher) - high, "H_duplicate_slots": duplicate, "H_duplicate_questions": int(duplicate > 0)})
    rows.append({"qid": qid, "L_slots": len(lower), "H_slots": len(higher), "H_unique": len(set(h_ids)), "H_duplicate_slots": duplicate, "H_new_vs_L40": len(set(h_ids) - set(l_ids)), "L_ids": l_ids, "H_ids": h_ids})
out["offline_pool_counts"] = dict(counts)
out["offline_pool_rows"] = rows
target = HERE / "R12_ATOM_REVIEW_REPRO_20261024.json"
target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"output": str(target), "counts": counts, "collisions": out["collision_repros"], "controls": out["control_branch_repros"]}, ensure_ascii=False, indent=2))
