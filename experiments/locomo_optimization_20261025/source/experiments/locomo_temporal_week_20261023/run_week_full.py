"""W0/W1 experiment runner: prepare / offline / evaluate / report.

Design follows NEXT_ADJUSTMENT.md sections 5-11:
  * arms B, W0, W1 only differ by week anchoring on top of frozen C1
  * offline builds all 861 requests and reports the changed union
  * evaluate screens the changed union first; the full run stays available
  * runtime hashes are re-verified before any model call
  * request caches and counters are persisted, answer/judge hashes recorded
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import sqlite3
import sys
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

RUN_ROOT = Path(r"F:\codex数据库memory\locomo_temporal_week_20261023")
BENCH = Path(r"F:\worktable\research\bench")
FULL_Q = BENCH / "reports/f1022b/aml_questions.json"
FULL_C = BENCH / "reports/f1022b/aml_search.json"
SOURCE = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\frozen\aml_sources.sqlite")
A0_SRC = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\patch\temporal_evidence.py")
V1_SRC = Path(r"F:\codex数据库memory\locomo_smoke300_20261022\source_evidence.py")
ARMS_DIR = RUN_ROOT / "arms"
ARMS = ("b", "w0", "w1")
EXPECTED = {"2": 167, "1": 77, "4": 581, "3": 36}
PROTECTED_EXTRA = {"conv-47#q0088", "conv-48#q0080"}
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


@contextmanager
def ro_db(path: Path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def load_module(path: Path, name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_turns(user_id: str, source_ids) -> dict:
    ids = list(dict.fromkeys(str(s) for s in source_ids if s))
    out = {}
    if not ids or not user_id:
        return out
    with ro_db(SOURCE) as db:
        for start in range(0, len(ids), 200):
            batch = ids[start:start + 200]
            marks = ",".join("?" for _ in batch)
            for row in db.execute(
                f"SELECT id,user_id,role,content,observed_at FROM source_turns WHERE user_id=? AND id IN ({marks})",
                (user_id, *batch),
            ):
                out[row["id"]] = {
                    "source_id": row["id"], "user_id": row["user_id"], "role": row["role"],
                    "content": row["content"], "observed_at": row["observed_at"],
                }
    return out


def canonical(rows) -> str:
    payload = [{"id": str(r.get("id") or ""), "content": str(r.get("content") or ""),
                "score": r.get("score"), "created_at": r.get("created_at")} for r in rows]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ------------------------------------------------------------------ prepare ---

def run_prepare(args) -> int:
    frozen = RUN_ROOT / "frozen"
    frozen.mkdir(parents=True, exist_ok=True)
    questions = read_json(FULL_Q)["questions"]
    candidates = read_json(FULL_C)["results"]
    qids = {q["question_id"] for q in questions}
    cids = {c["question_id"] for c in candidates}
    counts = defaultdict(int)
    for q in questions:
        counts[str(q.get("category"))] += 1
    problems = []
    if len(questions) != 861 or len(qids) != 861:
        problems.append("questions not 861 unique")
    if len(candidates) != 861 or len(cids) != 861:
        problems.append("candidates not 861 unique")
    if qids != cids:
        problems.append("question/candidate qid mismatch")
    if dict(counts) != EXPECTED:
        problems.append(f"category counts {dict(counts)}")
    if problems:
        print("PREPARE FAIL:", problems)
        return 2

    (frozen / "questions.json").write_bytes(FULL_Q.read_bytes())
    (frozen / "candidates.json").write_bytes(FULL_C.read_bytes())
    for name, src in (("a0_temporal_evidence.py", A0_SRC), ("v1_source_evidence.py", V1_SRC)):
        (frozen / name).write_bytes(src.read_bytes())
    for name in ("week_ext.py", "arm_b.py", "arm_w0.py", "arm_w1.py"):
        (frozen / name).write_bytes((ARMS_DIR / name).read_bytes())

    from runners.aml_run import _score_endpoints
    endpoints = _score_endpoints()
    experiment = {
        "created_at": now(),
        "run_root": str(RUN_ROOT),
        "questions": str(frozen / "questions.json"),
        "candidates": str(frozen / "candidates.json"),
        "source_snapshot": str(SOURCE),
        "arms": {"b": "frozen C1", "w0": "C1 + anchored last week/weekend", "w1": "W0 + calendar range"},
        "primary_comparison": "w0-b",
        "secondary_comparisons": ["w1-w0", "w1-b"],
        "model": {"answer_model": endpoints["answer_model"], "judge_model": endpoints["judge_model"],
                  "answer_host": endpoints.get("answer_base", "").split("//")[-1].split("/")[0],
                  "judge_host": endpoints.get("judge_base", "").split("//")[-1].split("/")[0]},
        "temperature": 0,
        "seed": 20261023,
        "budget_chars": 8000,
        "cache_protocol": "share one sample per identical full answer request and per identical judge request within one replicate",
        "hashes": {
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
        },
        "counts": {"questions": 861, "categories": dict(counts)},
    }
    write_json(RUN_ROOT / "experiment.json", experiment)
    print(json.dumps({"prepare": "ok", "model": experiment["model"]["answer_model"]}, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ offline ---

def build_arms(rows_b, original_rows, turns, query):
    import importlib

    b_mod = load_module(RUN_ROOT / "frozen/arm_b.py", "arm_b_mod")
    w0_mod = load_module(RUN_ROOT / "frozen/arm_w0.py", "arm_w0_mod")
    w1_mod = load_module(RUN_ROOT / "frozen/arm_w1.py", "arm_w1_mod")
    out = {}
    out["b"], tb, _ = b_mod.annotate(original_rows, turns, query)
    out["w0"], tw0, c0 = w0_mod.annotate(original_rows, turns, query)
    out["w1"], tw1, c1 = w1_mod.annotate(original_rows, turns, query)
    return out, {"b": tb, "w0": tw0, "w1": tw1}


def run_offline(args) -> int:
    config = read_json(RUN_ROOT / "experiment.json")
    out_dir = RUN_ROOT / "offline"
    questions = {q["question_id"]: q for q in read_json(FULL_Q)["questions"]}
    candidates = {c["question_id"]: c for c in read_json(FULL_C)["results"]}
    v1 = load_module(RUN_ROOT / "frozen/v1_source_evidence.py", "v1_mod")

    class Store:
        def __init__(self):
            self.path = str(SOURCE)

        def connect(self):
            return ro_db(SOURCE)

        def search(self, user_id, query, limit=20):
            return v1.SourceEvidenceStore.search(self, user_id, query, limit)

    store = Store()
    os.environ["TIMEM_SOURCE_EVIDENCE"] = "1"
    arms_mod = {
        "b": load_module(RUN_ROOT / "frozen/arm_b.py", "arm_b_mod"),
        "w0": load_module(RUN_ROOT / "frozen/arm_w0.py", "arm_w0_mod"),
        "w1": load_module(RUN_ROOT / "frozen/arm_w1.py", "arm_w1_mod"),
    }

    from benchmark_data.aml_models import EvalQuestion
    from evaluators.aml_score import answer_prompt, format_memories

    diff_rows, trace_rows = [], []
    changed_union = set()
    failures = defaultdict(list)
    changed_counts = defaultdict(int)

    for qid, q in questions.items():
        cand = candidates[qid]
        user_id = cand["user_id"]
        query = q["question"]
        options = q.get("options") or None
        c0 = v1.ground_evidence(user_id, query, cand.get("data") or [], 100, store=store)
        ids = [str(s) for r in c0 for s in (r.get("source_ids") or [])]
        turns = read_turns(user_id, ids)
        rows = {}
        traces = {}
        for arm in ARMS:
            rows[arm], traces[arm], _changed = arms_mod[arm].annotate([dict(r) for r in c0], turns, query)
        base_hash = hashlib.sha256(canonical(c0).encode()).hexdigest()
        hashes = {arm: hashlib.sha256(canonical(rows[arm]).encode()).hexdigest() for arm in ARMS}
        cat = str(q.get("category"))
        protected = cat != "2" and qid not in PROTECTED_EXTRA
        changed = {arm: hashes[arm] != base_hash for arm in ("w0", "w1")}
        if changed["w0"] or changed["w1"]:
            changed_union.add(qid)
            for arm in ("w0", "w1"):
                if changed[arm]:
                    changed_counts[arm] += 1
            if protected:
                failures["protected_changed"].extend(
                    {"question_id": qid, "arm": arm} for arm in ("w0", "w1") if changed[arm])

        # C1 prefix must stay untouched in W0/W1
        for arm in ("w0", "w1"):
            for new, old in zip(rows[arm], rows["b"]):
                if not str(new.get("content") or "").startswith(str(old.get("content") or "")):
                    failures["c1_prefix_broken"].append({"question_id": qid, "arm": arm})

        for arm in ("w0", "w1"):
            for entry in traces[arm]:
                if entry.get("schema_version") != "week-anchor-v1":
                    continue
                if entry.get("status") != "resolved":
                    continue
                turn = turns.get(entry["source_id"])
                if turn is None:
                    failures["cross_user"].append({"question_id": qid, "arm": arm})
                    continue
                start, end = entry["span"]
                if turn["content"][start:end] != entry["raw_phrase"]:
                    failures["span_mismatch"].append({"question_id": qid, "arm": arm})
                if entry["precision"] not in {"week", "weekend"}:
                    failures["precision_invalid"].append({"question_id": qid, "arm": arm, "p": entry["precision"]})
                if arm == "w0" and entry.get("calendar_range"):
                    failures["w0_has_range"].append(qid)
                if arm == "w1" and entry["rule"] == "anchor_last_weekend" and entry.get("calendar_range") is None:
                    pass  # allowed: weekend anchors on Sat/Sun deliberately have no range

        for arm in ARMS:
            kept = len(format_memories(rows[arm], question=query, options=options))
            if arm != "b" and kept < len(format_memories(rows["b"], question=query, options=options)):
                failures["budget_drop"].append({"question_id": qid, "arm": arm})

        diff_rows.append({
            "question_id": qid, "category": cat, "protected": protected,
            "changed": changed, "hashes": {**{"c1": base_hash}, **hashes},
            "annotations": {arm: sum(1 for t in traces[arm] if t.get("status") == "resolved") for arm in ("w0", "w1")},
        })
        for arm in ("w0", "w1"):
            for entry in traces[arm]:
                trace_rows.append({"question_id": qid, "arm": arm, **entry})

    summary = {
        "generated_at": now(), "mode": "offline", "questions": len(diff_rows),
        "changed_counts": dict(changed_counts),
        "changed_union": sorted(changed_union),
        "protected_changed": len(failures["protected_changed"]),
        "failures": {k: len(v) for k, v in failures.items()},
        "failure_samples": {k: v[:8] for k, v in failures.items()},
        "passed": all(len(v) == 0 for k, v in failures.items() if k != "w0_has_range"),
    }
    write_json(out_dir / "input_diff.json", {"summary": summary, "questions": diff_rows})
    with (out_dir / "week_trace.jsonl").open("w", encoding="utf-8") as fh:
        for item in trace_rows:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    write_json(out_dir / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("questions", "changed_counts", "protected_changed", "passed")}, ensure_ascii=False))
    print("changed union:", len(changed_union))
    return 0 if summary["passed"] else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["prepare", "offline", "evaluate", "report"])
    parser.add_argument("--subset", default="changed", choices=["changed", "all"])
    parser.add_argument("--replicate", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.mode == "prepare":
        return run_prepare(args)
    if args.mode == "offline":
        return run_offline(args)
    if args.mode == "evaluate":
        from week_evaluate import run_evaluate
        return asyncio.run(run_evaluate(args))
    from week_report import run_report
    return run_report(args)


if __name__ == "__main__":
    raise SystemExit(main())
