"""Full 861 answering: B/L/H with evidence sharing, judging, categories, pairing."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

BENCH = Path(r"F:\worktable\research\bench")
FULL = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_full_20261025")
QUESTIONS = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023\frozen/questions.json")
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))


def question_item(q: dict) -> dict:
    return {"dataset_id": q.get("dataset_id") or "locomo_refined", "question_id": q["question_id"],
            "question": q["question"], "gold": q.get("gold") or "", "category": q.get("category"),
            "options": q.get("options"), "preference": q.get("preference")}


def score_request(item: dict, evidence: str, endpoints: dict) -> dict:
    """Public request identity; deliberately excludes authentication secrets."""
    from runners.aml_run import _question_from_row
    from evaluators.aml_score import answer_prompt, judge_prompt

    question = _question_from_row(item)
    return {
        "protocol": "locomo-question-cache-v2",
        "item": item,
        "answer": {"base_url": endpoints["answer_base"], "model": endpoints["answer_model"],
                   "temperature": 0, "messages": [{"role": "user", "content":
                       answer_prompt(item["dataset_id"], question, evidence)}]},
        "judge": {"base_url": endpoints["judge_base"], "model": endpoints["judge_model"],
                  "temperature": 0, "prompt_template":
                      judge_prompt(item["dataset_id"], question, "__GENERATED_ANSWER__")},
    }


def score_cache_key(item: dict, evidence: str, endpoints: dict) -> str:
    serialized = json.dumps(score_request(item, evidence, endpoints), ensure_ascii=False,
                            sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def summarize(questions: dict, *, unique_scored: int, endpoints: dict) -> dict:
    stats = {a: {"correct": 0, "total": 0} for a in ("B", "L", "H")}
    by_cat = defaultdict(lambda: {a: [0, 0] for a in ("B", "L", "H")})
    pair = Counter()
    evidence_text_keys = set()
    for qid in questions:
        p = FULL / "answers" / f"{qid.replace('#','_')}.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        if set(rec["arms"]) != {"B", "L", "H"}:
            raise ValueError(f"Incomplete arms: {qid}")
        cat = rec["category"]
        for arm in ("B", "L", "H"):
            evidence_file = FULL / "evidence" / arm / f"{qid.replace('#','_')}.json"
            evidence = json.loads(evidence_file.read_text(encoding="utf-8"))["evidence"]
            evidence_text_keys.add(hashlib.sha256(evidence.encode("utf-8")).hexdigest())
            ok = int(rec["arms"][arm]["correct"])
            stats[arm]["correct"] += ok
            stats[arm]["total"] += 1
            by_cat[cat][arm][0] += ok
            by_cat[cat][arm][1] += 1
        for first, second in (("L", "B"), ("H", "L"), ("H", "B")):
            pair[f"{first}-{second}_{rec['arms'][first]['correct']}-{rec['arms'][second]['correct']}"] += 1
    return {"questions": len(questions), "unique_evidence_scored": len(evidence_text_keys),
            "unique_evidence_texts": len(evidence_text_keys),
            "unique_score_requests": unique_scored, "cache_protocol": "locomo-question-cache-v2",
            "stats": stats,
            "by_category": {c: {a: {"correct": v[a][0], "n": v[a][1]} for a in ("B", "L", "H")}
                            for c, v in by_cat.items()},
            "pairing": dict(pair), "answer_model": endpoints["answer_model"],
            "judge_model": endpoints["judge_model"]}


async def main() -> int:
    from runners.aml_run import _score_endpoints, _score_question_row

    questions = {q["question_id"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    out = FULL / "answers"
    out.mkdir(exist_ok=True)
    endpoints = _score_endpoints()
    sem = asyncio.Semaphore(4)
    cache: dict[str, dict] = {}

    async def score(qid: str, evidence: str):
        item = question_item(questions[qid])
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
            key = score_cache_key(question_item(questions[qid]), text, endpoints)
            if key not in cache:
                cache[key] = await score(qid, text)
            result = cache[key]
            for arm in arms:
                record["arms"][arm] = {"correct": bool(result.get("correct")),
                                       "generated": result.get("generated"), "request_sha": key}
        (out / f"{qid.replace('#','_')}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = summarize(questions, unique_scored=len(cache), endpoints=endpoints)
    (FULL / "RESULTS_FULL.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"unique_scored": len(cache), "stats": summary["stats"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
