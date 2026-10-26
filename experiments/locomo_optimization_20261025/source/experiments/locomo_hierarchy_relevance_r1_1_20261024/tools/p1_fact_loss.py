"""Diagnostic: are the facts needed by dev questions present in the derived store,
in the raw source, or lost during summarisation? No model calls.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

GROUNDED = Path(r"F:\codex数据库memory\locomo_hierarchy_grounded_20261024")
R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
R11 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_1_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
SOURCE_DB = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\frozen\aml_sources.sqlite")

STOP = set("the a an and or of to in on at for from with by is are was were be been it its this that these those i you he she they we my your his her their our".split())
TOKEN = re.compile(r"[a-z0-9']+")


def content_tokens(text: str) -> set:
    return {t for t in TOKEN.findall((text or "").lower()) if t not in STOP and len(t) > 2}


def main() -> int:
    dev = json.loads((R1 / "dev_sample.json").read_text(encoding="utf-8"))["question_ids"]
    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    store = json.loads((GROUNDED / "store.json").read_text(encoding="utf-8"))
    facts_by_conv = {}
    for fact in store["facts"].values():
        facts_by_conv.setdefault(fact["conv"], []).append(fact)
    fact_tokens = {conv: [content_tokens(f["text"]) for f in items] for conv, items in facts_by_conv.items()}

    con = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    rows = []
    stats = Counter()
    for qid in dev:
        q = questions[qid]
        conv = q["sample_id"]
        gold = str(q.get("gold") or "")
        gtok = content_tokens(gold)
        if not gtok:
            stats["empty_gold_tokens"] += 1
            continue
        # fact coverage: any single fact covering >=60% of gold content tokens
        best_fact = 0.0
        for tokens in fact_tokens.get(conv, []):
            if not tokens:
                continue
            best_fact = max(best_fact, len(gtok & tokens) / len(gtok))
        # source coverage
        msgs = con.execute("SELECT content FROM source_turns WHERE user_id LIKE ?", (f"%:{conv}%",)).fetchall()
        best_src = 0.0
        for msg in msgs:
            tokens = content_tokens(msg["content"])
            if tokens:
                best_src = max(best_src, len(gtok & tokens) / len(gtok))
        if best_fact >= 0.6:
            bucket = "in_facts"
        elif best_src >= 0.6:
            bucket = "lost_in_summary"
        else:
            bucket = "absent_everywhere"
        stats[bucket] += 1
        rows.append({"question_id": qid, "category": str(q.get("category")), "bucket": bucket,
                     "best_fact_cover": round(best_fact, 2), "best_source_cover": round(best_src, 2)})
    con.close()

    by_cat = Counter((r["category"], r["bucket"]) for r in rows)
    out = {"schema": "r1.1-fact-loss-diagnostic-v1", "questions": len(rows), "stats": dict(stats),
           "by_category": {f"{c}:{b}": n for (c, b), n in sorted(by_cat.items())}, "rows": rows}
    (R11 / "P1_FACT_LOSS.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"questions": len(rows), "stats": dict(stats),
                      "by_category": out["by_category"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
