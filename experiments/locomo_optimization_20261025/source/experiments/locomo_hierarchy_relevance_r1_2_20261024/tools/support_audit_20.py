"""R1.2 step 3 prep: fixed 20-question semantic audit sheet + visibility audit."""
from __future__ import annotations

import json
import random
import sqlite3
import sys
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R11 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_1_20261024")
R12 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
SOURCE = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\frozen\aml_sources.sqlite")
sys.path.insert(0, str(GROUNDED))
import grounded as g  # noqa: E402

SEED = 20261024


def main() -> int:
    dev = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"]
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    loss = {r["question_id"]: r for r in json.loads((R11 / "P1_FACT_LOSS.json").read_text(encoding="utf-8"))["rows"]}
    store = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))
    facts = store["facts"]
    retriever = g.Retriever()

    absent = [q for q in dev if loss.get(q, {}).get("bucket") == "absent_everywhere"]
    inside = [q for q in dev if loss.get(q, {}).get("bucket") == "in_facts"]
    rng = random.Random(SEED)

    def take(pool, cat, n):
        picked = [q for q in pool if str(questions[q].get("category")) == cat]
        rng.shuffle(picked)
        return picked[:n]

    sample = (take(absent, "2", 4) + take(absent, "3", 4) + take(absent, "1", 3) + take(absent, "4", 1)
              + take(inside, "2", 2) + take(inside, "4", 2) + take(inside, "1", 2) + take(inside, "3", 2))
    sample = list(dict.fromkeys(sample))

    con = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = []
    for qid in sample:
        q = questions[qid]
        conv = q["sample_id"]
        base, _ = retriever.windows(conv, q["question"], all_levels=False)
        b_facts = [f for w in base for f in w["fact_ids"]]
        b_texts = [facts[f]["text"] for f in b_facts if f in facts]
        src = con.execute("SELECT id, observed_at, role, content FROM source_turns WHERE user_id LIKE ? ORDER BY observed_at",
                          (f"%:{conv}%",)).fetchall()
        rows.append({
            "question_id": qid, "category": str(q.get("category")),
            "question": q["question"], "gold": q.get("gold"),
            "diagnosis_bucket": loss.get(qid, {}).get("bucket"),
            "source_messages": len(src),
            "b_facts": len(b_facts),
            "auto_source_token_overlap": round(max(
                (len(g.words(q.get("gold") or "") & g.words(m["content"])) / max(1, len(g.words(q.get("gold") or ""))))
                for m in src), 3) if src else 0.0,
            "auto_fact_token_overlap": round(max(
                (len(g.words(q.get("gold") or "") & g.words(t)) / max(1, len(g.words(q.get("gold") or ""))))
                for t in b_texts), 3) if b_texts else 0.0,
            "audit": {"S_source": "not_audited", "D_derived": "not_audited",
                      "C_L_candidates": "not_audited", "C_H_candidates": "not_audited",
                      "B_evidence": "not_audited", "answer_judge": "not_run"},
        })
    con.close()

    out = {"schema": "r1.2-support-audit-20-v1", "seed": SEED, "questions": len(rows),
           "sampling": "12 absent_everywhere (4/4/3/1 by category) + 8 in_facts (2 each)", "rows": rows}
    (R12 / "SUPPORT_AUDIT_20.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# R1.2 语义来源核对（20 题）", "",
             f"- 抽样：12 道 absent_everywhere（时间 4 / 推理 4 / 多跳 3 / 单跳 1）+ 8 道 in_facts（每类 2）",
             f"- 随机种子：{SEED}", "",
             "| qid | 类别 | 诊断桶 | 原文重合 | B 重合 | S | D | C_L | C_H | B |",
             "|---|---|---|---:|---:|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['question_id']} | {r['category']} | {r['diagnosis_bucket']} | "
                     f"{r['auto_source_token_overlap']:.2f} | {r['auto_fact_token_overlap']:.2f} | "
                     f"未审 | 未审 | 未审 | 未审 | 未审 |")
    lines += ["", "说明：自动列只给词面重合度，用于辅助定位；S/D/C_L/C_H/B 五层状态需人工核对后填写，",
              "未核对项保持 not_audited，不得外推为全量结论。"]
    (R12 / "SUPPORT_AUDIT_20.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"questions": len(rows), "absent": sum(1 for r in rows if r["diagnosis_bucket"] == "absent_everywhere")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
