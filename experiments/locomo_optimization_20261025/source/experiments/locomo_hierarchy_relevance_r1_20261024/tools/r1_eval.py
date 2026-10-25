"""R1 evaluate + report for the prepared two-arm evidence."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

R1 = Path(r"F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024")
BENCH = Path(r"F:\worktable\research\bench")
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

ARMS = ("L", "H")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


async def run_evaluate(args) -> int:
    from runners.aml_run import _score_endpoints, _score_question_row

    phase_dir = R1 / "requests" / args.phase
    protocol = read_json(phase_dir / "protocol.json")
    qids = protocol["question_ids"]
    out_dir = R1 / "eval" / args.phase
    partial_path = out_dir / "results.partial.json"
    state = read_json(partial_path, {"results": {}})
    results = state["results"]
    questions_doc = json.loads((R1.parent / "locomo_temporal_week_20261023/frozen/questions.json").read_text(encoding="utf-8"))
    gold_by_qid = {q["question_id"]: q for q in questions_doc["questions"]}
    endpoints = _score_endpoints()
    sem = asyncio.Semaphore(args.concurrency)

    async def one(qid: str, arm: str) -> dict:
        payload = read_json(phase_dir / arm / f"{qid.replace('#', '_')}.json")
        q = gold_by_qid[qid]
        item = {"dataset_id": payload.get("dataset_id") or "locomo_refined",
                "question_id": qid, "question": payload["question"],
                "gold": q.get("gold") or "", "category": q.get("category"),
                "options": q.get("options"), "preference": q.get("preference")}
        # gold is only read inside the evaluator step, never by retrieval
        from benchmark_data.aml_models import EvalQuestion  # noqa: F401
        async with sem:
            result = await _score_question_row(item, payload["evidence"], **endpoints)
        return {
            "question_id": qid, "arm": arm, "correct": bool(result.get("correct")),
            "method": result.get("method"), "generated": result.get("generated"),
            "evidence_tokens": payload["audit"].get("evidence_tokens"),
            "has_upper": payload["audit"].get("has_upper_window"),
        }

    pending = [(qid, arm) for qid in qids for arm in ARMS if f"{qid}:{arm}" not in results]
    print(f"pending {len(pending)} / {len(qids)*2}", flush=True)
    for start in range(0, len(pending), 8):
        batch = pending[start:start + 8]
        done = await asyncio.gather(*(one(qid, arm) for qid, arm in batch))
        for record in done:
            results[f"{record['question_id']}:{record['arm']}"] = record
        write_json(partial_path, {"results": results})
        print(f"done {len(results)}", flush=True)

    summary = summarize(results, qids, args.phase)
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary["arms"], ensure_ascii=False))
    return 0


def summarize(results, qids, phase) -> dict:
    questions = json.loads((R1.parent / "locomo_temporal_week_20261023/frozen/questions.json").read_text(encoding="utf-8"))["questions"]
    cat = {q["question_id"]: str(q.get("category")) for q in questions}
    arms = {}
    for arm in ARMS:
        per = defaultdict(lambda: {"n": 0, "correct": 0})
        total = 0
        for qid in qids:
            rec = results.get(f"{qid}:{arm}")
            if not rec:
                continue
            c = cat[qid]
            per[c]["n"] += 1
            per[c]["correct"] += int(rec["correct"])
            total += int(rec["correct"])
        arms[arm] = {"correct": total, "total": sum(v["n"] for v in per.values()), "by_category": dict(per)}
    w2r = sum(1 for q in qids if results.get(f"{q}:L", {}).get("correct") is False and results.get(f"{q}:H", {}).get("correct") is True)
    r2w = sum(1 for q in qids if results.get(f"{q}:L", {}).get("correct") is True and results.get(f"{q}:H", {}).get("correct") is False)
    n = w2r + r2w
    p = 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(w2r, r2w) + 1)) / 2 ** n)
    changed = sum(1 for q in qids if (results.get(f"{q}:L", {}).get("has_upper") is False) and (results.get(f"{q}:H", {}).get("has_upper") is True))
    return {
        "phase": phase, "questions": len(qids), "arms": arms,
        "wrong_to_right": w2r, "right_to_wrong": r2w, "net": w2r - r2w, "mcnemar_p": p,
        "questions_with_upper_evidence": changed,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="dev")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    return asyncio.run(run_evaluate(args))


if __name__ == "__main__":
    raise SystemExit(main())
