"""Screening evaluation for B / W0 / W1 on the changed-request union."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

RUN_ROOT = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023")
BENCH = Path(r"F:\worktable\research\bench")
SOURCE = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\frozen\aml_sources.sqlite")
ARMS = ("b", "w0", "w1")
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

from run_week_full import (  # noqa: E402
    canonical, load_module, read_json, read_turns, sha, write_json,
)


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def runtime_hashes() -> dict:
    frozen = RUN_ROOT / "frozen"
    return {
        "questions": sha(frozen / "questions.json"),
        "candidates": sha(frozen / "candidates.json"),
        "source": sha(SOURCE),
        "a0": sha(frozen / "a0_temporal_evidence.py"),
        "v1": sha(frozen / "v1_source_evidence.py"),
        "week_ext": sha(frozen / "week_ext.py"),
        "arm_b": sha(frozen / "arm_b.py"),
        "arm_w0": sha(frozen / "arm_w0.py"),
        "arm_w1": sha(frozen / "arm_w1.py"),
        "bench_aml_score": sha(BENCH / "evaluators/aml_score.py"),
        "bench_aml_run": sha(BENCH / "runners/aml_run.py"),
    }


class Cache:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.hits = 0
        self.calls = 0
        self.reuse: dict[str, int] = defaultdict(int)

    async def get(self, key: str, factory):
        if key in self.values:
            self.hits += 1
            self.reuse[key] += 1
            return self.values[key], True
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self.values:
                self.hits += 1
                self.reuse[key] += 1
                return self.values[key], True
            value = await factory()
            self.values[key] = value
            self.calls += 1
            return value, False


async def run_evaluate(args) -> int:
    from benchmark_data.aml_models import EvalQuestion
    from evaluators.aml_score import answer_prompt, complete_chat, format_memories, judge_prompt, score_mcq
    from runners.aml_run import _question_from_row, _score_endpoints, verdict_correct_wrong

    config = read_json(RUN_ROOT / "experiment.json")
    actual = runtime_hashes()
    diff = {k: (config["hashes"].get(k), v) for k, v in actual.items() if config["hashes"].get(k) != v}
    if diff:
        raise SystemExit(f"runtime hash mismatch, refusing to run: {diff}")

    offline = read_json(RUN_ROOT / "offline/summary.json")
    if not offline or not offline.get("passed"):
        raise SystemExit("offline audit not passed")

    subset_all = offline["changed_union"] if args.subset == "changed" else None
    questions = {q["question_id"]: q for q in read_json(RUN_ROOT / "frozen/questions.json")["questions"]}
    candidates = {c["question_id"]: c for c in read_json(RUN_ROOT / "frozen/candidates.json")["results"]}
    qids = subset_all or list(questions)
    if args.limit:
        qids = qids[: args.limit]

    out_dir = RUN_ROOT / "evaluate" / args.subset / f"rep{args.replicate}"
    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out_dir / "results.partial.json"
    state = read_json(partial, {"results": {}, "counters": {}})
    results = state["results"]

    v1 = load_module(RUN_ROOT / "frozen/v1_source_evidence.py", "v1_mod")
    arms = {
        "b": load_module(RUN_ROOT / "frozen/arm_b.py", "arm_b_mod"),
        "w0": load_module(RUN_ROOT / "frozen/arm_w0.py", "arm_w0_mod"),
        "w1": load_module(RUN_ROOT / "frozen/arm_w1.py", "arm_w1_mod"),
    }
    os.environ["TIMEM_SOURCE_EVIDENCE"] = "1"

    class Store:
        def __init__(self):
            self.path = str(SOURCE)

        def connect(self):
            from run_week_full import ro_db
            return ro_db(SOURCE)

        def search(self, user_id, query, limit=20):
            return v1.SourceEvidenceStore.search(self, user_id, query, limit)

    store = Store()
    endpoints = _score_endpoints()
    answers, judges = Cache(), Cache()
    semaphore = asyncio.Semaphore(args.concurrency)

    def rows_for(qid: str) -> dict:
        cand = candidates[qid]
        query = questions[qid]["question"]
        c0 = v1.ground_evidence(cand["user_id"], query, cand.get("data") or [], 100, store=store)
        ids = [str(s) for r in c0 for s in (r.get("source_ids") or [])]
        turns = read_turns(cand["user_id"], ids)
        out = {}
        for arm in ARMS:
            out[arm], _trace, _changed = arms[arm].annotate([dict(r) for r in c0], turns, query)
        return out

    async def judge(item: dict, generated: str) -> dict:
        question = _question_from_row(item)
        prompt = judge_prompt(item["dataset_id"], question, generated)
        if prompt is None:
            return score_mcq(question, generated)
        key = sha_text(json.dumps({"model": endpoints["judge_model"], "base": endpoints["judge_base"],
                              "prompt": prompt, "temperature": 0, "rep": args.replicate}, sort_keys=True))

        async def call():
            async with semaphore:
                return await complete_chat(base_url=endpoints["judge_base"], api_key=endpoints["judge_key"],
                                           model=endpoints["judge_model"], prompt=prompt)

        last_error = None
        for attempt in range(3):
            text, _shared = await judges.get(key, call)
            verdict = await verdict_correct_wrong(lambda: asyncio.sleep(0, result=text))
            if verdict.get("method") != "judge_parse_error":
                return {**verdict, "judge_request": key}
            last_error = text
            judges.values.pop(key, None)  # never cache a parse failure as success
            await asyncio.sleep(2)
        return {"correct": False, "score": 0.0, "method": "judge_parse_error", "judge_raw": last_error, "judge_request": key}

    async def one_arm(qid: str, arm: str, rows: list) -> dict:
        q = questions[qid]
        item = dict(q)
        options = q.get("options") or None
        memories = format_memories(rows, question=q["question"], options=options)
        prompt = answer_prompt(q.get("dataset_id") or "locomo_refined", _question_from_row(item), memories)
        key = sha_text(json.dumps({"model": endpoints["answer_model"], "base": endpoints["answer_base"],
                              "prompt": prompt, "temperature": 0, "rep": args.replicate}, sort_keys=True))

        async def call():
            async with semaphore:
                return await complete_chat(base_url=endpoints["answer_base"], api_key=endpoints["answer_key"],
                                           model=endpoints["answer_model"], prompt=prompt)

        generated, shared = await answers.get(key, call)
        verdict = await judge(item, generated)
        return {
            "question_id": qid, "arm": arm, "category": str(q.get("category")),
            "generated": generated, "correct": bool(verdict.get("correct")),
            "method": verdict.get("method"), "judge_raw": verdict.get("judge_raw"),
            "answer_request": key, "judge_request": verdict.get("judge_request"),
            "answer_shared": shared, "answered_at": datetime.now(timezone.utc).isoformat(),
        }

    async def one_question(qid: str) -> None:
        if all(f"{qid}:{arm}" in results for arm in ARMS):
            return
        rows = rows_for(qid)
        order = list(ARMS)
        random.Random(f"{config['seed']}:{qid}").shuffle(order)
        for arm in order:
            results[f"{qid}:{arm}"] = await one_arm(qid, arm, rows[arm])
        counters = {
            "unique_answer_requests": len(answers.values), "answer_hits": answers.hits,
            "unique_judge_requests": len(judges.values), "judge_hits": judges.hits,
            "logical_results": len(results),
        }
        state["counters"] = counters
        write_json(partial, {"results": results, "counters": counters})

    pending = [qid for qid in qids if not all(f"{qid}:{arm}" in results for arm in ARMS)]
    print(f"subset={args.subset} pending={len(pending)}/{len(qids)}", flush=True)
    for start in range(0, len(pending), 16):
        await asyncio.gather(*(one_question(qid) for qid in pending[start:start + 16]))
        print(f"done {len(results)//3}/{len(qids)}", flush=True)

    summary = summarize(results, questions, qids, args.subset, args.replicate, state["counters"], endpoints)
    write_json(out_dir / "aml_score.json", summary)
    print(json.dumps({arm: summary["arms"][arm]["correct"] for arm in ARMS}, ensure_ascii=False))
    return 0


def summarize(results, questions, qids, subset, replicate, counters, endpoints) -> dict:
    done = [qid for qid in qids if all(f"{qid}:{arm}" in results for arm in ARMS)]
    arms = {}
    for arm in ARMS:
        per = defaultdict(lambda: {"n": 0, "correct": 0})
        total = 0
        for qid in done:
            cat = str(questions[qid].get("category"))
            per[cat]["n"] += 1
            per[cat]["correct"] += int(results[f"{qid}:{arm}"]["correct"])
            total += int(results[f"{qid}:{arm}"]["correct"])
        arms[arm] = {"correct": total, "total": len(done), "by_category": dict(per)}

    def pairs(a: str, b: str):
        w2r = r2w = 0
        for qid in done:
            x = results[f"{qid}:{a}"]["correct"]
            y = results[f"{qid}:{b}"]["correct"]
            if y and not x:
                w2r += 1
            elif x and not y:
                r2w += 1
        return {"wrong_to_right": w2r, "right_to_wrong": r2w}

    return {
        "subset": subset, "replicate": replicate, "completed": len(done), "total": len(qids),
        "incomplete": len(done) != len(qids),
        "arms": arms,
        "comparisons": {"w0-b": pairs("b", "w0"), "w1-w0": pairs("w0", "w1"), "w1-b": pairs("b", "w1")},
        "counters": counters,
        "model": endpoints["answer_model"], "judge_model": endpoints["judge_model"],
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
