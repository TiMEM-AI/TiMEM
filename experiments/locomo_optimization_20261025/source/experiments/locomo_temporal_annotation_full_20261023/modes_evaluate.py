"""Evaluate mode: four arms, one sample shared per identical full request.

The answer request and the judge request are cached separately by their full
content hash, so arms whose inputs are byte-identical never re-sample the model.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

RUN_ROOT = Path(r"F:\codex数据库memory\locomo_temporal_annotation_full_20261023")
BENCH = Path(r"F:\worktable\research\bench")
SOURCE_SNAPSHOT = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\frozen\aml_sources.sqlite")
ARMS = ("c0", "c1", "c2", "c3")
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))


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


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(config: dict) -> str:
    payload = {
        "questions": config["hashes"]["questions"],
        "candidates": config["hashes"]["candidates"],
        "source": config["hashes"]["source_snapshot"],
        "arms": config["hashes"]["arms"],
        "v1": config["hashes"]["v1_source_evidence"],
        "a0": config["hashes"]["a0_temporal_evidence"],
        "model": config["model"],
        "temperature": config["temperature"],
        "seed": config["seed"],
        "budget": config["budget_chars"],
        "max_sentence": config["max_source_sentence_chars"],
        "cache": config["cache_protocol"],
    }
    return sha(json.dumps(payload, sort_keys=True, ensure_ascii=False))


class CallCache:
    """Singleflight cache keyed by the full request hash."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.hits = 0
        self.calls = 0

    async def get(self, key: str, factory):
        if key in self.data:
            self.hits += 1
            return self.data[key]
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self.data:
                self.hits += 1
                return self.data[key]
            value = await factory()
            self.data[key] = value
            self.calls += 1
            return value


async def run_evaluate(args) -> int:
    from benchmark_data.aml_models import EvalQuestion
    from evaluators.aml_score import answer_prompt, complete_chat, format_memories, judge_prompt, score_mcq
    from runners.aml_run import _question_from_row, _score_endpoints, verdict_correct_wrong

    config = read_json(Path(args.config))
    root = Path(config["run_root"])
    out_dir = root / "evaluate" / f"rep{args.replicate}"
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = fingerprint(config)

    questions = {q["question_id"]: q for q in read_json(Path(config["questions"]))["questions"]}
    candidates = {c["question_id"]: c for c in read_json(Path(config["candidates"]))["results"]}
    offline = read_json(root / "offline/input_diff.json")
    if not offline or not offline["summary"].get("passed"):
        raise SystemExit("offline audit has not passed; refusing to call models")

    partial_path = out_dir / "results.partial.json"
    state = read_json(partial_path, {"fingerprint": fp, "results": {}})
    if state.get("fingerprint") != fp:
        raise SystemExit("resume fingerprint mismatch; refusing to continue")

    sys.path.insert(0, str(root / "frozen"))
    from modes_offline import ReadOnlyIndex, load_module, make_v1_store, read_turns

    frozen = root / "frozen"
    v1_mod = load_module(frozen / "v1_source_evidence.py", "frozen_v1")
    arms = {
        "c1": load_module(frozen / "arm_c1.py", "arm_c1_mod"),
        "c2": load_module(frozen / "arm_c2.py", "arm_c2_mod"),
        "c3": load_module(frozen / "arm_c3.py", "arm_c3_mod"),
    }
    os.environ["TIMEM_SOURCE_EVIDENCE"] = "1"
    index = ReadOnlyIndex(SOURCE_SNAPSHOT)
    store = make_v1_store(v1_mod, SOURCE_SNAPSHOT)

    endpoints = _score_endpoints()
    answers = CallCache()
    judges = CallCache()
    semaphore = asyncio.Semaphore(args.concurrency)
    results = state["results"]
    stats = {"answer_calls": 0, "answer_hits": 0, "judge_calls": 0, "judge_hits": 0}

    def build_rows(qid: str, question: dict) -> dict:
        cand = candidates[qid]
        user_id = cand["user_id"]
        query = question["question"]
        c0 = v1_mod.ground_evidence(user_id, query, cand.get("data") or [], 100, store=store)
        ids = [str(sid) for r in c0 for sid in (r.get("source_ids") or [])]
        turns = read_turns(index, user_id, ids)
        rows = {"c0": c0}
        for arm in ("c1", "c2", "c3"):
            rows[arm], _trace, _changed = arms[arm].annotate([dict(r) for r in c0], turns, query)
        return rows

    async def judge_one(item: dict, generated: str) -> dict:
        question = _question_from_row(item)
        prompt = judge_prompt(item["dataset_id"], question, generated)
        if prompt is None:
            return score_mcq(question, generated)
        key = sha(json.dumps({
            "model": endpoints["judge_model"], "base": endpoints["judge_base"],
            "prompt": prompt, "temperature": 0, "replicate": args.replicate,
        }, sort_keys=True))

        async def call():
            async with semaphore:
                return await complete_chat(base_url=endpoints["judge_base"], api_key=endpoints["judge_key"],
                                           model=endpoints["judge_model"], prompt=prompt)

        async def fetch():
            return await judges.get(key, call)

        return await verdict_correct_wrong(fetch)

    async def one_arm(qid: str, arm: str, rows: list) -> dict:
        question = questions[qid]
        item = dict(question)
        query = question["question"]
        options = question.get("options") or None
        memories = format_memories(rows, question=query, options=options)
        prompt = answer_prompt(question.get("dataset_id") or "locomo_refined", _question_from_row(item), memories)
        key = sha(json.dumps({
            "model": endpoints["answer_model"], "base": endpoints["answer_base"],
            "prompt": prompt, "temperature": 0, "replicate": args.replicate,
        }, sort_keys=True))

        async def call():
            async with semaphore:
                return await complete_chat(base_url=endpoints["answer_base"], api_key=endpoints["answer_key"],
                                           model=endpoints["answer_model"], prompt=prompt)

        generated = await answers.get(key, call)
        verdict = await judge_one(item, generated)
        return {
            "question_id": qid, "arm": arm, "category": str(question.get("category")),
            "generated": generated, "correct": bool(verdict.get("correct")),
            "method": verdict.get("method"), "judge_raw": verdict.get("judge_raw"),
            "answer_request": key,
            "answer_shared": False,
        }

    async def one_question(qid: str) -> None:
        if all(f"{qid}:{arm}" in results for arm in ARMS):
            return
        question = questions[qid]
        rows = build_rows(qid, question)
        order = list(ARMS)
        random.Random(f"{config['seed']}:{qid}").shuffle(order)
        for arm in order:
            record = await one_arm(qid, arm, rows[arm])
            results[f"{qid}:{arm}"] = record
        write_json(partial_path, {"fingerprint": fp, "results": results})

    pending = [qid for qid in questions if not all(f"{qid}:{arm}" in results for arm in ARMS)]
    limit = int(getattr(args, "limit", 0) or 0)
    if limit:
        pending = pending[:limit]
    print(f"pending questions: {len(pending)} / {len(questions)}", flush=True)

    started = datetime.now(timezone.utc).isoformat()
    chunk = 20
    for start in range(0, len(pending), chunk):
        batch = pending[start:start + chunk]
        await asyncio.gather(*(one_question(qid) for qid in batch))
        print(f"done {len(results)//4}/{len(questions)} questions", flush=True)

    stats.update({"answer_calls": answers.calls, "answer_hits": answers.hits,
                  "judge_calls": judges.calls, "judge_hits": judges.hits})
    summary = summarize(results, questions, args.replicate, fp, started, stats, endpoints)
    write_json(out_dir / "aml_score.json", summary)
    write_json(out_dir / "requests.json", {
        "answer_requests": len(answers.data), "judge_requests": len(judges.data),
        "answer_hits": answers.hits, "judge_hits": judges.hits,
        "logical_evaluations": len(questions) * len(ARMS),
    })
    print(json.dumps({arm: summary["arms"][arm]["correct"] for arm in ARMS}, ensure_ascii=False))
    return 0


def summarize(results, questions, replicate, fp, started, stats, endpoints) -> dict:
    done = [qid for qid in questions if all(f"{qid}:{arm}" in results for arm in ARMS)]
    arms = {}
    for arm in ARMS:
        per_cat = defaultdict(lambda: {"n": 0, "correct": 0})
        total = 0
        for qid in done:
            q = questions[qid]
            record = results[f"{qid}:{arm}"]
            cat = str(q.get("category"))
            per_cat[cat]["n"] += 1
            per_cat[cat]["correct"] += int(record["correct"])
            total += int(record["correct"])
        arms[arm] = {"correct": total, "total": len(done), "by_category": dict(per_cat)}

    def delta_pairs(a: str, b: str):
        w2r = r2w = both = neither = 0
        for qid in done:
            x = results[f"{qid}:{a}"]["correct"]
            y = results[f"{qid}:{b}"]["correct"]
            if y and not x:
                w2r += 1
            elif x and not y:
                r2w += 1
            elif x:
                both += 1
            else:
                neither += 1
        return {"wrong_to_right": w2r, "right_to_wrong": r2w, "both": both, "neither": neither}

    comparisons = {
        "c3-c0": delta_pairs("c0", "c3"),
        "c2-c1": delta_pairs("c1", "c2"),
        "c3-c2": delta_pairs("c2", "c3"),
    }
    return {
        "replicate": replicate,
        "fingerprint": fp,
        "completed": len(done),
        "total_questions": len(questions),
        "incomplete": len(done) != len(questions),
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "model": endpoints["answer_model"],
        "judge_model": endpoints["judge_model"],
        "arms": arms,
        "comparisons": comparisons,
        "cache": stats,
        "temperature": 0,
    }
