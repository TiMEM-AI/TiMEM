"""Fresh paired scoring: 300 questions, V1 and V2, interleaved, one model per run.

Both arms are scored in the same run/conditions, so the comparison does not mix
historical model versions. Resume is supported through a partial file.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(r"F:\worktable\research\timem-aml")
BENCH = Path(r"F:\worktable\research\bench")
OUT = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023")
FROZEN = Path(r"F:\codex数据库memory\locomo_smoke300_20261022")
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_arms(args, questions, baseline):
    """Compute V1 and V2 rows offline for every question."""
    from offline_mode import load_module, make_readonly_store

    snapshot = Path(args.source_db)
    frozen_mod = load_module(FROZEN / "source_evidence.py", "frozen_source_evidence")
    v2_mod = load_module(BACKEND / "server/source_evidence.py", "v2_source_evidence")
    v1_store = make_readonly_store(frozen_mod, snapshot, "v1")
    v2_store = make_readonly_store(v2_mod, snapshot, "v2")

    os.environ["TIMEM_SOURCE_EVIDENCE"] = "1"
    os.environ["TIMEM_TEMPORAL_RENDERER"] = "off"
    arms = {}
    for qid, question in questions.items():
        hit = baseline.get(qid)
        if hit is None:
            continue
        base_rows = hit.get("data") or []
        user_id = hit.get("user_id")
        query = question["question"]
        v1_rows = frozen_mod.ground_evidence(user_id, query, base_rows, 100, store=v1_store)
        os.environ["TIMEM_TEMPORAL_RENDERER"] = args.feature
        v2_rows = v2_mod.ground_evidence(user_id, query, base_rows, 100, store=v2_store)
        arms[qid] = {"user_id": user_id, "v1": v1_rows, "v2": v2_rows}
    os.environ["TIMEM_TEMPORAL_RENDERER"] = "off"
    return arms


async def run_fresh(args) -> int:
    from evaluators.aml_score import format_memories
    from runners.aml_run import _score_endpoints, _score_question_row

    output = Path(args.output)
    manifest = read_json(Path(args.frozen_run) / "manifest.json", None)
    questions = {q["question_id"]: q for q in manifest["questions"]}
    baseline = {r["question_id"]: r for r in read_json(Path(args.frozen_run) / "baseline/aml_search.json", {"results": []})["results"]}

    arms = build_arms(args, questions, baseline)
    endpoints = _score_endpoints()
    provenance = {
        "mode": "fresh-paired",
        "feature": args.feature,
        "questions": len(arms),
        "answer_model": endpoints["answer_model"],
        "judge_model": endpoints["judge_model"],
        "answer_base": endpoints["answer_base"],
        "judge_base": endpoints["judge_base"],
        "source_evidence_sha256": hashlib.sha256((BACKEND / "server/source_evidence.py").read_bytes()).hexdigest(),
        "temporal_evidence_sha256": hashlib.sha256((BACKEND / "server/temporal_evidence.py").read_bytes()).hexdigest(),
        "concurrency": args.concurrency,
        "started_at": utc_now(),
    }
    write_json(output / "provenance.json", provenance)

    partial = output / "aml_score.partial.json"
    scored = {r["question_id"]: r for r in (read_json(partial, {"results": []})["results"])}

    semaphore = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()
    started = time.perf_counter()

    async def score_arm(qid: str, arm: str, rows: list) -> dict:
        question = questions[qid]
        options = question.get("options") or None
        memories = format_memories(rows, question=question["question"], options=options)
        hit = {"question_id": qid, "returned": len(rows), "data": rows}
        for attempt in range(3):
            try:
                result = await _score_question_row(question, memories, **endpoints, hit=hit)
                if result.get("method") == "judge_parse_error":
                    raise RuntimeError("judge parse failure")
                return result
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(3)

    async def one(qid: str) -> None:
        async with semaphore:
            v1 = await score_arm(qid, "v1", arms[qid]["v1"])
            v2 = await score_arm(qid, "v2", arms[qid]["v2"])
        async with lock:
            scored[qid] = {"question_id": qid, "category": str(questions[qid].get("category")), "v1": v1, "v2": v2}
            write_json(partial, {"provenance": provenance, "results": list(scored.values())})
            done = len(scored)
            if done % 10 == 0 or done == len(arms):
                print(f"scored {done}/{len(arms)} elapsed={time.perf_counter() - started:.0f}s", flush=True)

    pending = [qid for qid in arms if qid not in scored]
    await asyncio.gather(*(one(qid) for qid in pending))

    rows = [scored[qid] for qid in arms if qid in scored]
    summary = {"provenance": provenance, "total": len(rows)}

    def rate(arm: str) -> dict:
        by_cat = {}
        for item in rows:
            cat = item["category"]
            slot = by_cat.setdefault(cat, {"n": 0, "correct": 0})
            slot["n"] += 1
            slot["correct"] += 1 if item[arm].get("correct") else 0
        total_correct = sum(1 for item in rows if item[arm].get("correct"))
        return {"correct": total_correct, "total": len(rows), "by_category": by_cat}

    v1_rate, v2_rate = rate("v1"), rate("v2")
    wrong_to_right = sum(1 for i in rows if not i["v1"].get("correct") and i["v2"].get("correct"))
    right_to_wrong = sum(1 for i in rows if i["v1"].get("correct") and not i["v2"].get("correct"))
    summary.update({
        "v1": v1_rate,
        "v2": v2_rate,
        "wrong_to_right": wrong_to_right,
        "right_to_wrong": right_to_wrong,
        "net": wrong_to_right - right_to_wrong,
        "judge_parse_errors": {
            "v1": sum(1 for i in rows if i["v1"].get("method") == "judge_parse_error"),
            "v2": sum(1 for i in rows if i["v2"].get("method") == "judge_parse_error"),
        },
    })
    write_json(output / "aml_score.json", summary)

    for arm in ("v1", "v2"):
        write_json(output / arm / "aml_search.json", {
            "results": [
                {"question_id": qid, "user_id": arms[qid]["user_id"], "returned": len(arms[qid][arm]), "data": arms[qid][arm]}
                for qid in arms
            ]
        })

    def pct(slot):
        return 100.0 * slot["correct"] / slot["total"] if slot["total"] else 0.0

    print(json.dumps({
        "v1": f"{v1_rate['correct']}/{v1_rate['total']} = {pct(v1_rate):.2f}%",
        "v2": f"{v2_rate['correct']}/{v2_rate['total']} = {pct(v2_rate):.2f}%",
        "wrong_to_right": wrong_to_right,
        "right_to_wrong": right_to_wrong,
        "net": wrong_to_right - right_to_wrong,
    }, ensure_ascii=False))
    print(f"report: {output / 'aml_score.json'}")
    return 0
