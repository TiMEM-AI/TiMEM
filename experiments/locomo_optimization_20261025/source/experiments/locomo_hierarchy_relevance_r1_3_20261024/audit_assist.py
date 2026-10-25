"""R1.3 stage A: assemble evidence for the 20-question semantic audit."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
R13 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
SOURCE = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\frozen\aml_sources.sqlite")
sys.path.insert(0, str(GROUNDED))
import grounded as g  # noqa: E402


def main() -> int:
    audit = json.loads((R12 / "SUPPORT_AUDIT_20.json").read_text(encoding="utf-8"))["rows"]
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    facts = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))["facts"]
    folded = json.loads((R1 / "routes_r1.json").read_text(encoding="utf-8"))["routes"]
    retriever = g.Retriever()
    con = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    out = []
    for row in audit:
        qid = row["question_id"]
        q = questions[qid]
        conv = q["sample_id"]
        base, _ = retriever.windows(conv, q["question"], all_levels=False)
        b_ids = [f for w in base for f in w["fact_ids"]]
        b_set = set(b_ids)
        gold_tokens = g.words(q.get("gold") or "")
        msgs = con.execute(
            "SELECT id, observed_at, role, content FROM source_turns WHERE user_id LIKE ? ORDER BY observed_at",
            (f"%:{conv}%",)).fetchall()
        ranked_msgs = sorted(msgs, key=lambda m: -len(gold_tokens & g.words(m["content"])))[:3]
        cand_facts = sorted(
            (f for f in facts.values() if f["conv"] == conv and f["id"] not in b_set),
            key=lambda f: -len(gold_tokens & g.words(f["text"])))[:8]
        upper = []
        qt = set(g.words(q["question"]))
        for route in folded.values():
            if route["conv"] != conv:
                continue
            alias_tokens = set()
            for a in route.get("aliases", [])[:3]:
                alias_tokens |= set(g.words(a))
            if not (qt & alias_tokens):
                continue
            for target in route["target_fact_ids"]:
                if target in b_set or target not in facts:
                    continue
                if facts[target]["text"] not in [u["text"] for u in upper]:
                    upper.append({"fact_id": target, "text": facts[target]["text"],
                                  "observed_at": facts[target]["observed_at"]})
                break
            if len(upper) >= 5:
                break
        out.append({
            "question_id": qid, "category": row["category"], "question": q["question"], "gold": q.get("gold"),
            "source_top3": [{"id": m["id"], "observed_at": m["observed_at"], "role": m["role"],
                             "text": m["content"][:240]} for m in ranked_msgs],
            "fact_top8": [{"fact_id": f["id"], "observed_at": f["observed_at"], "text": f["text"][:200]}
                          for f in cand_facts],
            "b_top5": [{"fact_id": f, "text": facts[f]["text"][:200]} for f in b_ids[:5]],
            "upper_top5": upper,
        })
    con.close()
    (R13 / "AUDIT_EVIDENCE_20.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"assembled {len(out)} questions -> AUDIT_EVIDENCE_20.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
