"""Offline readiness check: does H now reach candidates L cannot, on 20 and on 861?"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R13B = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
sys.path.insert(0, str(GROUNDED))
sys.path.insert(0, str(R13B))
import grounded as g  # noqa: E402
from r13b_prepare import H_LOW, H_UP, L_SLOTS, build_pools, pick20  # noqa: E402


def main() -> int:
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    facts = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["facts"]
    retriever = g.Retriever()

    picked = pick20(questions, json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"])
    rows20 = []
    for qid in picked:
        q = questions[qid]
        conv, query = q["sample_id"], q["question"]
        base, _ = retriever.windows(conv, query, all_levels=False)
        guard = not g.compositional_query(query)
        L, H, lower, upper = build_pools(conv, query, base, facts, folded, retriever)
        lid = {x["id"] for x in L}
        hid = {x["id"] for x in H}
        rows20.append({"qid": qid, "guard": guard, "L": len(L), "H": len(H),
                       "upper": len(upper), "H_only": len(hid - lid), "L_only": len(lid - hid)})
    s20 = {"questions": len(rows20), "guard": sum(r["guard"] for r in rows20),
           "with_upper": sum(1 for r in rows20 if r["upper"] > 0),
           "H_differs_from_L": sum(1 for r in rows20 if r["H_only"] or r["L_only"]),
           "upper_total": sum(r["upper"] for r in rows20)}

    # full 861 offline intervention estimate
    counts = Counter()
    samples = []
    for qid, q in questions.items():
        conv, query = q["sample_id"], q["question"]
        if not g.compositional_query(query):
            counts["guard"] += 1
            continue
        base, _ = retriever.windows(conv, query, all_levels=False)
        L, H, lower, upper = build_pools(conv, query, base, facts, folded, retriever)
        lid = {x["id"] for x in L}
        hid = {x["id"] for x in H}
        counts["planned"] += 1
        if upper:
            counts["with_upper"] += 1
        if hid - lid:
            counts["H_differs"] += 1
            if len(samples) < 5:
                samples.append({"qid": qid, "upper": len(upper), "H_only": len(hid - lid)})
    report = {
        "schema": "r1.3b-readiness-v2",
        "sample20": s20,
        "full861_offline": dict(counts),
        "planned_calls_estimate": counts["planned"],
        "H_differs_samples": samples,
        "runtime_estimate_hours": round(counts["planned"] * 75 / 3600, 1),
    }
    (R13B / "READINESS_V2.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
