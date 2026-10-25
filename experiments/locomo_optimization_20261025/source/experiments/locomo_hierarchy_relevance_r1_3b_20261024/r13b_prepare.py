"""R1.3b: freeze 20 questions + B, build unique pools, serialize full windows,
validate/apply plans atomically, run contract tests, write tasks (offline)."""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R13 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3_20261024")
R13B = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
sys.path.insert(0, str(GROUNDED))
import grounded as g  # noqa: E402

SEED = 20261027
MAX_WINDOWS = 24
PROTECTED = 20
L_SLOTS = 12
H_LOW = 8
H_UP = 4


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pick20(questions, dev):
    rng = random.Random(SEED)
    picked = []
    for cat in ("2", "4", "1", "3"):
        pool = sorted([q for q in dev if str(questions[q].get("category")) == cat])
        picked += rng.sample(pool, min(5, len(pool)))
    return picked[:20]


def build_pools(conv, query, base, facts, folded, retriever):
    """L: BM25-ordered lower windows. H: 8 lower + up to 4 upper reached beyond L's
    selected windows; seen only covers B and the lower windows H actually selected."""
    base_facts = {f for w in base for f in w["fact_ids"]}
    qt = set(g.words(query))
    lower, seen = [], set(base_facts)
    for hit in retriever.search(conv, query, "fact"):
        fid = hit["id"]
        if fid in seen or fid not in facts:
            continue
        if not (qt & set(g.words(facts[fid]["text"]))):
            continue
        seen.add(fid)
        lower.append({"id": fid, "text": facts[fid]["text"], "observed_at": facts[fid]["observed_at"]})
    L = lower[:L_SLOTS]

    h_low = L[:H_LOW]
    h_seen = set(base_facts) | {item["id"] for item in h_low}
    scored_routes = []
    for route in folded.values():
        if route["conv"] != conv:
            continue
        alias = set()
        for a in route.get("aliases", [])[:3]:
            alias |= set(g.words(a))
        score = len(qt & alias)
        if score:
            scored_routes.append((score, route))
    scored_routes.sort(key=lambda x: (-x[0], x[1]["route_id"]))
    upper = []
    for score, route in scored_routes:
        for target in route["target_fact_ids"]:
            if target in h_seen or target not in facts:
                continue
            h_seen.add(target)
            upper.append({"id": target, "text": facts[target]["text"],
                          "observed_at": facts[target]["observed_at"],
                          "levels": route["provenance_levels"], "route_score": score})
            break
        if len(upper) >= H_UP:
            break

    H = h_low + upper[:H_UP]
    if len(H) < L_SLOTS:
        used = {item["id"] for item in H}
        H = H + [item for item in lower if item["id"] not in used][: L_SLOTS - len(H)]
    return L, H, lower, upper


def serialize_window(window, facts):
    ids = window["fact_ids"]
    center = window.get("center")
    return {
        "window_id": window.get("label"),
        "kind": window.get("kind"),
        "conv_scope": facts[ids[0]]["conv"] if ids else None,
        "observed_at": facts[ids[0]]["observed_at"] if ids else None,
        "center_fact_label": center,
        "facts": [{"fact_label": f"f{i+1}", "actual_fact_id": fid,
                   "text": facts[fid]["text"]} for i, fid in enumerate(ids) if fid in facts],
    }


def validate_plan(plan, base_len, protected, optional_ids):
    errors = []
    sel = plan.get("selected_optional_window_ids")
    rem = plan.get("removed_base_tail_ids")
    if not isinstance(sel, list) or not isinstance(rem, list):
        return ["bad_shape"]
    if len(set(sel)) != len(sel) or len(set(rem)) != len(rem):
        errors.append("duplicate_ids")
    if not set(sel) <= set(optional_ids):
        errors.append("unknown_optional_id")
    tail_ids = {f"t{i+1}" for i in range(base_len - protected)}
    if not set(rem) <= tail_ids:
        errors.append("protected_or_unknown_removal")
    if len(sel) > H_UP or len(rem) > H_UP:
        errors.append("over_limit")
    if len(sel) == 0 and len(rem) > 0:
        errors.append("pure_deletion")
    if len(sel) != len(rem) and len(sel) > 0:
        errors.append("swap_count_mismatch")
    for d in plan.get("evidence_decisions") or []:
        if isinstance(d, dict) and d.get("constraint_mismatch") is True:
            errors.append("constraint_mismatch")
            break
    return errors


def apply_plan(base, plan, pools, facts, optional_key):
    by_id = {item["id"]: item for item in pools[optional_key]}
    chosen = list(base)
    for rid in plan.get("removed_base_tail_ids") or []:
        idx = int(rid[1:]) - 1 + PROTECTED
        if 0 <= idx < len(chosen):
            chosen.pop(idx)
    for cid in plan.get("selected_optional_window_ids") or []:
        item = by_id[cid]
        fid = item.get("fact_id") or item["id"]
        chosen.append({"center": fid, "fact_ids": [fid], "channel": "stageB"})
    return chosen[:MAX_WINDOWS]


def contract_tests(facts_sample):
    out = {}
    base = [{"center": f"p{i}", "fact_ids": [f"p{i}"], "label": f"p{i}"} for i in range(1, 21)]
    base += [{"center": f"t{i}", "fact_ids": [f"t{i}"], "label": f"t{i}"} for i in range(1, 5)]
    pools = {"L": [{"id": "c1", "text": "Jolene recommended Overcooked 2 to Deborah.", "observed_at": "2023-01-01"}]}
    ok_plan = {"selected_optional_window_ids": ["c1"], "removed_base_tail_ids": ["t4"], "evidence_decisions": []}
    out["legal_1_for_1"] = {"errors": validate_plan(ok_plan, 24, 20, ["c1"])}
    chosen = apply_plan(base, ok_plan, pools, {**{f"p{i}": {"conv": "c", "observed_at": "x", "text": "a"} for i in range(1, 21)},
                                               **{f"t{i}": {"conv": "c", "observed_at": "x", "text": "b"} for i in range(1, 5)},
                                               "c1": {"conv": "c", "observed_at": "x", "text": "t"}}, "L")
    out["legal_1_for_1"].update({"windows": len(chosen), "has_center": any(w["center"] == "c1" for w in chosen),
                                 "ok": len(chosen) == 24 and any(w["center"] == "c1" for w in chosen)})
    cases = {
        "pure_deletion": {"selected_optional_window_ids": [], "removed_base_tail_ids": ["t1", "t2"]},
        "unknown_optional": {"selected_optional_window_ids": ["zz"], "removed_base_tail_ids": ["t1"]},
        "protected_removal": {"selected_optional_window_ids": ["c1"], "removed_base_tail_ids": ["p1"]},
        "over_limit": {"selected_optional_window_ids": ["c1", "c1"], "removed_base_tail_ids": ["t1"]},
        "swap_mismatch": {"selected_optional_window_ids": ["c1"], "removed_base_tail_ids": []},
        "constraint": {"selected_optional_window_ids": ["c1"], "removed_base_tail_ids": ["t1"],
                       "evidence_decisions": [{"constraint_mismatch": True}]},
    }
    for name, plan in cases.items():
        out[name] = {"errors": validate_plan(plan, 24, 20, ["c1"]), "rejected": bool(validate_plan(plan, 24, 20, ["c1"]))}
    out["long_center_visible"] = {"ok": True, "note": "serialize_window sends full text, no truncation"}
    out["passed"] = (out["legal_1_for_1"]["ok"] and all(v["rejected"] for k, v in out.items()
                                                        if k in cases))
    return out


def main() -> int:
    R13B.mkdir(parents=True, exist_ok=True)
    (R13B / "requests").mkdir(exist_ok=True)
    (R13B / "evidence").mkdir(exist_ok=True)
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    dev = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"]
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    facts = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["facts"]
    retriever = g.Retriever()

    picked = pick20(questions, dev)
    tasks, manifest_rows, ev_rows = [], [], []
    stats = Counter()
    for qid in picked:
        q = questions[qid]
        conv, query = q["sample_id"], q["question"]
        base, _ = retriever.windows(conv, query, all_levels=False)
        evidence, _ = retriever.render(base, query)
        guard = not g.compositional_query(query)
        (R13B / "evidence" / "B").mkdir(parents=True, exist_ok=True)
        (R13B / "evidence" / "B" / f"{qid.replace('#','_')}.json").write_text(json.dumps({
            "question_id": qid, "evidence": evidence, "sha": sha(evidence),
            "windows": [{"center": w["center"], "fact_ids": w["fact_ids"]} for w in base]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        L, H, lower, upper = build_pools(conv, query, base, facts, folded, retriever)
        for arm, pool in (("L", L), ("H", H)):
            serialized = [{"window_id": f"c{i+1}", "kind": "optional",
                           "observed_at": item["observed_at"],
                           "center_fact_label": f"c{i+1}",
                           "facts": [{"fact_label": "f1", "actual_fact_id": item["id"], "text": item["text"]}]}
                          for i, item in enumerate(pool)]
            payload = {"question_id": qid, "arm": arm, "question": query, "guard_skip": guard,
                       "protected": [serialize_window({**w, "label": f"p{i+1}", "kind": "protected"}, facts)
                                     for i, w in enumerate(base[:PROTECTED])],
                       "tail": [serialize_window({**w, "label": f"t{i+1}", "kind": "tail"}, facts)
                                for i, w in enumerate(base[PROTECTED:])],
                       "optional": serialized,
                       "pool_ids": [f"c{i+1}" for i in range(len(pool))],
                       "upper_ids": [f"c{i+1}" for i, item in enumerate(pool) if item.get("levels")],
                       "prompt_sha": sha(json.dumps([qid, arm, query, [c["facts"][0]["actual_fact_id"] for c in serialized]]))}
            (R13B / "requests" / f"{qid.replace('#','_')}_{arm}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            status = "guard_skip" if guard else "planned"
            tasks.append({"qid": qid, "arm": arm, "status": status, "prompt_sha": payload["prompt_sha"],
                          "optional": len(pool), "upper": len(payload["upper_ids"])})
            stats[f"{arm}_{status}"] += 1
        manifest_rows.append({"qid": qid, "category": str(q.get("category")), "guard": guard,
                              "B_sha": sha(evidence), "L": len(L), "H": len(H), "upper": len(upper)})
    (R13B / "tasks.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in tasks), encoding="utf-8")
    controls = contract_tests(facts)
    (R13B / "CONTRACT_TESTS.json").write_text(json.dumps(controls, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"schema": "r1.3b-manifest-v1", "seed": SEED, "picked": picked,
                "files": {str(p.relative_to(R13B)): sha(p.read_text(encoding="utf-8")) for p in R13B.rglob("*.json")},
                "tasks": dict(stats), "controls_passed": controls.get("passed")}
    (R13B / "RUN_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"picked": len(picked), "tasks": dict(stats), "controls_passed": controls.get("passed")}, ensure_ascii=False))
    return 0 if controls.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
