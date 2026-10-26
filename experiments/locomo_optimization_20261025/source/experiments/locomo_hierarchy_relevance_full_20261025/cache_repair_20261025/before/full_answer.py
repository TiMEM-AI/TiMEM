"""Full 861 answering: B/L/H with evidence sharing, judging, categories, pairing."""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

BENCH = Path(r"F:\worktable\research\bench")
FULL = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_full_20261025")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))


async def main() -> int:
    from runners.aml_run import _score_endpoints, _score_question_row

    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    out = FULL / "answers"
    out.mkdir(exist_ok=True)
    endpoints = _score_endpoints()
    sem = asyncio.Semaphore(4)
    cache: dict[str, dict] = {}

    async def score(qid: str, evidence: str):
        q = questions[qid]
        item = {"dataset_id": q.get("dataset_id") or "locomo_refined", "question_id": qid,
                "question": q["question"], "gold": q.get("gold") or "", "category": q.get("category"),
                "options": q.get("options"), "preference": q.get("preference")}
        async with sem:
            return await _score_question_row(item, evidence, **endpoints)

    for qid in questions:
        record = {"question_id": qid, "category": str(questions[qid].get("category")), "arms": {}}
        groups = defaultdict(list)
        texts = {}
        for arm in ("B", "L", "H"):
            p = FULL / "evidence" / arm / f"{qid.replace('#','_')}.json"
            if not p.exists():
                continue
            texts[arm] = json.loads(p.read_text(encoding="utf-8"))["evidence"]
            groups[texts[arm]].append(arm)
        for text, arms in groups.items():
            if text not in cache:
                cache[text] = await score(qid, text)
            result = cache[text]
            for arm in arms:
                record["arms"][arm] = {"correct": bool(result.get("correct")),
                                       "generated": result.get("generated")}
        (out / f"{qid.replace('#','_')}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    stats = {a: {"correct": 0, "total": 0} for a in ("B", "L", "H")}
    by_cat = defaultdict(lambda: {a: [0, 0] for a in ("B", "L", "H")})
    pair = Counter()
    for qid in questions:
        p = out / f"{qid.replace('#','_')}.json"
        if not p.exists():
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        cat = rec["category"]
        for arm in ("B", "L", "H"):
            if arm not in rec["arms"]:
                continue
            ok = int(rec["arms"][arm]["correct"])
            stats[arm]["correct"] += ok
            stats[arm]["total"] += 1
            by_cat[cat][arm][0] += ok
            by_cat[cat][arm][1] += 1
        if "B" in rec["arms"] and "L" in rec["arms"]:
            pair[f"L-B_{rec['arms']['L']['correct']}-{rec['arms']['B']['correct']}"] += 1
        if "L" in rec["arms"] and "H" in rec["arms"]:
            pair[f"H-L_{rec['arms']['H']['correct']}-{rec['arms']['L']['correct']}"] += 1
    summary = {"questions": len(questions), "unique_evidence_scored": len(cache), "stats": stats,
               "by_category": {c: {a: {"correct": v[a][0], "n": v[a][1]} for a in ("B", "L", "H")}
                               for c, v in by_cat.items()},
               "pairing": dict(pair), "answer_model": endpoints["answer_model"],
               "judge_model": endpoints["judge_model"]}
    (FULL / "RESULTS_FULL.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"unique_scored": len(cache), "stats": stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
