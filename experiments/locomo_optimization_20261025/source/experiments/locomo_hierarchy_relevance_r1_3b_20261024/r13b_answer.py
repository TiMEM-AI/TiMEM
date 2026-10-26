"""R1.3b step 6: answer+judge B/L/H on the frozen 20 questions, sharing identical evidence."""
from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

BENCH = Path(r"F:\worktable\research\bench")
R13B = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))


async def main() -> int:
    from runners.aml_run import _score_endpoints, _score_question_row

    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    picked = json.loads((R13B / "RUN_MANIFEST.json").read_text(encoding="utf-8"))["picked"]
    out = R13B / "answers"
    out.mkdir(exist_ok=True)
    endpoints = _score_endpoints()
    sem = asyncio.Semaphore(4)
    cache = {}

    async def score(qid: str, evidence: str):
        q = questions[qid]
        item = {"dataset_id": q.get("dataset_id") or "locomo_refined", "question_id": qid,
                "question": q["question"], "gold": q.get("gold") or "", "category": q.get("category"),
                "options": q.get("options"), "preference": q.get("preference")}
        async with sem:
            return await _score_question_row(item, evidence, **endpoints)

    async def run(qid: str):
        ev = {}
        for arm in ("B", "L", "H"):
            p = R13B / "evidence" / arm / f"{qid.replace('#','_')}.json"
            ev[arm] = json.loads(p.read_text(encoding="utf-8"))["evidence"]
        groups = defaultdict(list)
        for arm, text in ev.items():
            groups[text].append(arm)
        record = {"question_id": qid, "category": str(questions[qid].get("category")), "arms": {}}
        for text, arms in groups.items():
            if text not in cache:
                cache[text] = await score(qid, text)
            result = cache[text]
            for arm in arms:
                record["arms"][arm] = {"correct": bool(result.get("correct")),
                                       "generated": result.get("generated"),
                                       "method": result.get("method")}
        (out / f"{qid.replace('#','_')}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(qid, {a: record["arms"][a]["correct"] for a in ("B", "L", "H")}, flush=True)

    for qid in picked:
        await run(qid)

    stats = {a: {"correct": 0, "total": 0} for a in ("B", "L", "H")}
    by_cat = defaultdict(lambda: {a: [0, 0] for a in ("B", "L", "H")})
    for qid in picked:
        rec = json.loads((out / f"{qid.replace('#','_')}.json").read_text(encoding="utf-8"))
        cat = rec["category"]
        for arm in ("B", "L", "H"):
            ok = int(rec["arms"][arm]["correct"])
            stats[arm]["correct"] += ok
            stats[arm]["total"] += 1
            by_cat[cat][arm][0] += ok
            by_cat[cat][arm][1] += 1
    summary = {"questions": len(picked), "unique_evidence_scored": len(cache), "stats": stats,
               "by_category": {c: {a: {"correct": v[a][0], "n": v[a][1]} for a in ("B", "L", "H")}
                               for c, v in by_cat.items()},
               "model": endpoints["answer_model"], "judge_model": endpoints["judge_model"]}
    (R13B / "RESULTS_20.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"unique_scored": len(cache), "stats": stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
