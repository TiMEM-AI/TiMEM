"""R1.2 step 3b: build a 40-group candidate blind-audit sheet (no model calls).

Rows carry real candidate ids and visible text; arm labels and historical scores
are excluded. Human judgement fields stay blank/not_audited.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R11 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_1_20261024")
R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
sys.path.insert(0, str(GROUNDED))
import grounded as g  # noqa: E402

SEED = 20261024


def main() -> int:
    dev = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"]
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    facts = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["facts"]
    retriever = g.Retriever()
    rng = random.Random(SEED)

    rows = []
    for qid in dev:
        q = questions[qid]
        conv, query = q["sample_id"], q["question"]
        base, _ = retriever.windows(conv, query, all_levels=False)
        base_facts = {f for w in base for f in w["fact_ids"]}
        qt = set(g.words(query))
        cands = []
        for route in folded.values():
            if route["conv"] != conv:
                continue
            tokens = set()
            for alias in route.get("aliases", [])[:3]:
                tokens |= set(g.words(alias))
            if not (qt & tokens):
                continue
            for target in route["target_fact_ids"]:
                if target in base_facts or target not in facts:
                    continue
                text = facts[target]["text"]
                cands.append({"qid": qid, "question": query, "fact_id": target,
                              "text": text[:300], "observed_at": facts[target]["observed_at"],
                              "levels": route["provenance_levels"],
                              "alias_overlap": len(qt & tokens)})
                break
            if len(cands) >= 3:
                break
        if cands:
            rows.append(cands[0])
            if len(cands) > 1:
                rows.append(cands[1])
            if len(cands) > 2:
                rows.append(cands[2])
    rng.shuffle(rows)
    rows = rows[:40]

    sheet = [{
        "row": i + 1, "question_id": r["qid"], "question": r["question"],
        "candidate_fact_id": r["fact_id"], "candidate_text": r["text"],
        "observed_at": r["observed_at"], "route_levels": r["levels"],
        "hidden": {"arm": "not_shown", "historical_correctness": "not_shown"},
        "audit": {"supported": "not_audited", "relevance": "not_audited",
                  "new_answer_value": "not_audited", "would_replace_unique_support": "not_audited",
                  "decision": "not_audited", "notes": ""},
    } for i, r in enumerate(rows)]

    out = {"schema": "r1.2-candidate-audit-40-v1", "seed": SEED, "rows": len(sheet),
           "sampling": "B-external folded-route targets, <=3 per dev question, shuffled, first 40",
           "note": "arm labels and historical scores hidden; judgement fields left not_audited",
           "sheet": sheet}
    (R12 / "CANDIDATE_AUDIT_40.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# R1.2 候选盲审表（40 组）", "",
             "每行是 B 之外、可由折叠路由到达的真实候选；arm 与历史分数已隐藏。", "",
             "| # | qid | 候选事实 | 观察时间 | 层 | 支持 | 相关性 | 新答案值 | 可替换 | 决定 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sheet:
        text = r["candidate_text"].replace("|", " ")[:90]
        lines.append(f"| {r['row']} | {r['question_id']} | {text} | {str(r['observed_at'])[:10]} | "
                     f"{','.join(r['route_levels'])} | 未审 | 未审 | 未审 | 未审 | 未审 |")
    (R12 / "CANDIDATE_AUDIT_40.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"rows": len(sheet), "questions": len({r['question_id'] for r in sheet})}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
