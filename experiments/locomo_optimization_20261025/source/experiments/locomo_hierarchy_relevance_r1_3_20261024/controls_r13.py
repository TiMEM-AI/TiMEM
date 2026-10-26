"""R1.3 stage A: query-conditioned selector + full-capacity controls (offline)."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

R13 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3_20261024")
R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
sys.path.insert(0, str(R12))

from support_atoms import RELATIONS, build_atoms  # noqa: E402

NEG = re.compile(r"\b(not|never|n't|no longer)\b", re.I)


def stem_relation(token: str | None) -> str | None:
    if not token:
        return None
    t = token.lower()
    for suffix in ("ing", "ed", "s"):
        if t.endswith(suffix) and len(t) > len(suffix) + 3:
            return t[: -len(suffix)]
    return t


def _agent(text: str, relation: str | None):
    """Entity closest before the relation verb; None when undecidable."""
    if not relation:
        return None
    words = re.findall(r"[A-Za-z0-9']+", text)
    low = [stem_relation(w) for w in words]
    target = stem_relation(relation)
    if target not in low:
        return None
    idx = low.index(target)
    for w in reversed(words[:idx]):
        if w[:1].isupper() and w.lower() not in {"the", "a", "an"}:
            return w.lower()
    return None


def query_plan(query: str) -> dict:
    toks = re.findall(r"[a-z0-9']+", query.lower())
    relation = stem_relation(next((t for t in toks if t in RELATIONS or stem_relation(t) in {stem_relation(r) for r in RELATIONS}), None))
    return {"relation": relation, "negated": bool(NEG.search(query)),
            "agent": _agent(query, relation)}


def fact_is_negated(text: str) -> bool:
    return bool(NEG.search(text))


def conditioned_support(fact: dict, plan: dict) -> set:
    """Atoms relevant to the question: relation-bound values plus entity atoms."""
    atoms = build_atoms(fact)
    out = set()
    for atom in atoms:
        if atom.kind == "entity":
            out.add(atom.key())
            continue
        if plan["relation"] and stem_relation(atom.relation) != plan["relation"]:
            continue
        out.add(atom.key())
    return out


def select_conditioned(base, candidates, facts, query, *, max_windows=24, protected=20):
    plan = query_plan(query)
    by_id = {f["id"]: f for f in facts}
    selected = [dict(w) for w in base]

    def support(windows):
        out = set()
        for w in windows:
            for fid in w["fact_ids"]:
                if fid in by_id:
                    out |= conditioned_support(by_id[fid], plan)
        return out

    audit = {"accepted": [], "replaced": [], "rejected": []}
    covered = support(selected)
    for cand in candidates:
        ids = [x for x in cand["fact_ids"] if x in by_id]
        if not ids:
            audit["rejected"].append(("no_fact", cand.get("center")))
            continue
        if not plan["negated"] and any(fact_is_negated(by_id[x]["text"]) for x in ids):
            audit["rejected"].append(("polarity_mismatch", cand.get("center")))
            continue
        if plan["agent"]:
            agents = {_agent(by_id[x]["text"], plan["relation"]) for x in ids}
            agents.discard(None)
            if agents and plan["agent"] not in agents:
                audit["rejected"].append(("agent_mismatch", cand.get("center")))
                continue
        cand_support = support([{"fact_ids": ids}])
        gain = cand_support - covered
        if plan["relation"]:
            relation_bound = {a for a in gain if a[0] != "entity"}
            if not relation_bound:
                audit["rejected"].append(("no_relation_gain", cand.get("center")))
                continue
        if not gain:
            audit["rejected"].append(("no_gain", cand.get("center")))
            continue
        if len(selected) < max_windows:
            selected.append({"center": cand.get("center"), "fact_ids": ids, "channel": "cand"})
            covered |= cand_support
            audit["accepted"].append({"center": cand.get("center"), "ids": ids, "gain": len(gain)})
            continue
        placed = False
        for idx in range(len(selected) - 1, protected - 1, -1):
            victim = selected[idx]
            others = [w for i, w in enumerate(selected) if i != idx]
            victim_support = support([victim])
            after = support(others) | cand_support
            if victim_support - after:
                continue
            selected[idx] = {"center": cand.get("center"), "fact_ids": ids, "channel": "cand"}
            covered = after
            audit["replaced"].append({"removed": victim["fact_ids"], "added": ids})
            audit["accepted"].append({"center": cand.get("center"), "ids": ids, "gain": len(gain)})
            placed = True
            break
        if not placed:
            audit["rejected"].append(("no_safe_replacement", cand.get("center")))
    return selected, audit


# ------------------------------------------------------------------ controls ---

def fact(fid, text, obs="2023-05-01T00:00:00+00:00", conv="c1"):
    return {"id": fid, "conv": conv, "observed_at": obs, "text": text}


def win(center, ids):
    return {"center": center, "fact_ids": ids}


def full_base(facts, tail_texts):
    """24 windows: 20 protected + 4 tails; tails use given facts."""
    base = [win(f"p{i}", [f"p{i}"]) for i in range(1, 21)]
    base += [win(f"t{i}", [f"t{i}"]) for i in range(1, 5)]
    return base


def run_controls() -> dict:
    out = {}

    # 1. full-capacity safe replacement: tail 4 duplicates tail 3's support
    facts = [fact(f"p{i}", f"Person{i} likes item{i}.") for i in range(1, 21)]
    facts += [fact("t1", "Jolene recommended Zelda to Deborah."),
              fact("t2", "Jolene recommended Zelda to Deborah again."),
              fact("t3", "Someone went shopping."),
              fact("t4", "Another person slept.")]
    facts.append(fact("cand", "Jolene recommended Overcooked 2 to Deborah."))
    base = full_base(facts, None)
    selected, audit = select_conditioned(base, [win("cand", ["cand"])], facts,
                                         "Which games did Jolene recommend to Deborah?")
    out["full_capacity_safe_replacement"] = {
        "replaced": len(audit["replaced"]), "accepted": len(audit["accepted"]),
        "windows": len(selected), "ok": len(audit["replaced"]) == 1 and len(selected) == 24,
    }

    # 2. full-capacity unsafe: every tail carries unique support
    facts2 = [fact(f"p{i}", f"Person{i} likes item{i}.") for i in range(1, 21)]
    facts2 += [fact("t1", "Jolene recommended Zelda to Deborah."),
               fact("t2", "Deborah adopted a cat named Mochi."),
               fact("t3", "Jolene moved to Shanghai in April."),
               fact("t4", "Deborah started pottery classes.")]
    facts2.append(fact("cand", "Jolene recommended Overcooked 2 to Deborah."))
    base2 = full_base(facts2, None)
    selected2, audit2 = select_conditioned(base2, [win("cand", ["cand"])], facts2,
                                           "Which games did Jolene recommend to Deborah?")
    out["full_capacity_unsafe_replacement"] = {
        "replaced": len(audit2["replaced"]), "accepted": len(audit2["accepted"]),
        "windows": len(selected2), "ok": len(audit2["replaced"]) == 0 and len(audit2["accepted"]) == 0,
    }

    # 3. same person, different event, with free slots
    facts3 = [fact("a", "Jolene recommended Zelda to Deborah."),
              fact("b", "Jolene crashed her car near the market.")]
    selected3, audit3 = select_conditioned([win("a", ["a"])], [win("b", ["b"])], facts3,
                                           "Which games did Jolene recommend to Deborah?")
    out["same_person_different_event"] = {"accepted": len(audit3["accepted"]), "ok": len(audit3["accepted"]) == 0}

    # 4. new answer value, same relation
    facts4 = [fact("a", "Jolene recommended Zelda to Deborah."),
              fact("b", "Jolene recommended Overcooked 2 to Deborah.")]
    selected4, audit4 = select_conditioned([win("a", ["a"])], [win("b", ["b"])], facts4,
                                           "Which games did Jolene recommend to Deborah?")
    out["new_answer_value"] = {"accepted": len(audit4["accepted"]), "ok": len(audit4["accepted"]) == 1}

    # 5. negation must not be treated as the same support
    facts5 = [fact("a", "Jolene recommended Zelda to Deborah."),
              fact("b", "Jolene never recommended Overcooked 2 to Deborah.")]
    selected5, audit5 = select_conditioned([win("a", ["a"])], [win("b", ["b"])], facts5,
                                           "Which games did Jolene recommend to Deborah?")
    out["negation_mismatch"] = {"accepted": len(audit5["accepted"]), "ok": len(audit5["accepted"]) == 0}

    # 6. role swap: candidate swaps recommender and receiver
    facts6 = [fact("a", "Jolene recommended Zelda to Deborah."),
              fact("b", "Deborah recommended Overcooked 2 to Jolene.")]
    selected6, audit6 = select_conditioned([win("a", ["a"])], [win("b", ["b"])], facts6,
                                           "Which games did Jolene recommend to Deborah?")
    out["role_swap"] = {"accepted": len(audit6["accepted"]),
                        "note": "role binding is not enforced by atoms; recorded for audit"}

    out["passed"] = all(out[k]["ok"] for k in (
        "full_capacity_safe_replacement", "full_capacity_unsafe_replacement",
        "same_person_different_event", "new_answer_value", "negation_mismatch"))
    return out


if __name__ == "__main__":
    R13.mkdir(parents=True, exist_ok=True)
    result = run_controls()
    (R13 / "CONTROLS_R13.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 2)
