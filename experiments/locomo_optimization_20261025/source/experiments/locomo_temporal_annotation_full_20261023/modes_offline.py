"""Appended modes for run_annotation_full.py (offline / evaluate / report)."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

RUN_ROOT = Path(r"F:\codex数据库memory\locomo_temporal_annotation_full_20261023")
BENCH = Path(r"F:\worktable\research\bench")
BACKEND = Path(r"F:\worktable\research\timem-aml")
SOURCE_SNAPSHOT = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\frozen\aml_sources.sqlite")
PROTECTED_SINGLE_HOP = {"conv-47#q0088", "conv-48#q0080"}
ARMS = ("c0", "c1", "c2", "c3")
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))


# ------------------------------------------------------------------ helpers ---

def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class ReadOnlyIndex:
    def __init__(self, path: Path):
        self.path = str(path)

    def connect(self):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                yield db
            finally:
                db.close()

        return _cm()


def load_module(path: Path, name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_turns(index, user_id: str, source_ids) -> dict:
    ids = list(dict.fromkeys(str(s) for s in source_ids if s))
    if not ids or not user_id:
        return {}
    out = {}
    with index.connect() as db:
        for start in range(0, len(ids), 200):
            batch = ids[start:start + 200]
            marks = ",".join("?" for _ in batch)
            rows = db.execute(
                f"SELECT id,user_id,role,content,observed_at FROM source_turns "
                f"WHERE user_id=? AND id IN ({marks})",
                (user_id, *batch),
            ).fetchall()
            for row in rows:
                out[row["id"]] = {
                    "source_id": row["id"], "user_id": row["user_id"], "role": row["role"],
                    "content": row["content"], "observed_at": row["observed_at"],
                }
    return out


def canonical_public(rows) -> str:
    payload = [
        {
            "id": str(r.get("id") or ""),
            "content": str(r.get("content") or ""),
            "score": r.get("score"),
            "created_at": r.get("created_at"),
        }
        for r in rows
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_v1_store(frozen_mod, path: Path):
    class Store:
        def __init__(self):
            self.path = str(path)

        def connect(self):
            from contextlib import contextmanager

            @contextmanager
            def _cm():
                db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
                db.row_factory = sqlite3.Row
                try:
                    yield db
                finally:
                    db.close()

            return _cm()

        def search(self, user_id, query, limit=20):
            return frozen_mod.SourceEvidenceStore.search(self, user_id, query, limit)

    return Store()


def included_indices(records, question: str, options) -> list:
    import tiktoken

    from evaluators.aml_score import ANSWER_INPUT_TOKEN_BUDGET

    encoder = tiktoken.get_encoding("cl100k_base")
    reserved = len(encoder.encode(question))
    if options:
        reserved += len(encoder.encode("\n".join(options)))
    budget = max(0, ANSWER_INPUT_TOKEN_BUDGET - reserved)
    lines, used, kept = 0, 0, []
    for index, record in enumerate(records):
        content = str(record.get("content") or "").strip()
        if not content:
            continue
        created = str(record.get("created_at") or "").strip()
        line = f"- [{created}] {content}" if created else f"- {content}"
        cost = len(encoder.encode(line))
        if lines and used + cost > budget:
            break
        if not lines and cost > budget:
            kept.append(index)
            break
        kept.append(index)
        lines += 1
        used += cost
    return kept


# ------------------------------------------------------------------ offline ---

def run_offline(args) -> int:
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    root = Path(config["run_root"])
    out_dir = root / "offline"
    out_dir.mkdir(parents=True, exist_ok=True)

    questions = {q["question_id"]: q for q in json.loads(Path(config["questions"]).read_text(encoding="utf-8"))["questions"]}
    candidates = {c["question_id"]: c for c in json.loads(Path(config["candidates"]).read_text(encoding="utf-8"))["results"]}

    frozen_dir = root / "frozen"
    v1_mod = load_module(frozen_dir / "v1_source_evidence.py", "frozen_v1")
    arm_mods = {
        "c1": load_module(frozen_dir / "arm_c1.py", "arm_c1_mod"),
        "c2": load_module(frozen_dir / "arm_c2.py", "arm_c2_mod"),
        "c3": load_module(frozen_dir / "arm_c3.py", "arm_c3_mod"),
    }
    os.environ["TIMEM_SOURCE_EVIDENCE"] = "1"
    index = ReadOnlyIndex(SOURCE_SNAPSHOT)
    store = make_v1_store(v1_mod, SOURCE_SNAPSHOT)

    from evaluators.aml_score import answer_prompt, format_memories
    from benchmark_data.aml_models import EvalQuestion

    saved_v1 = {}
    saved_v2 = {}
    fresh_score = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\fresh")
    if (fresh_score / "v1/aml_search.json").exists():
        saved_v1 = {r["question_id"]: r for r in json.loads((fresh_score / "v1/aml_search.json").read_text(encoding="utf-8"))["results"]}
        saved_v2 = {r["question_id"]: r for r in json.loads((fresh_score / "v2/aml_search.json").read_text(encoding="utf-8"))["results"]}

    diff_rows = []
    traces = []
    failures = {"protected_changed": [], "span_mismatch": [], "precision_violation": [],
                "budget_drop": [], "cross_user": [], "c1_replay": [], "c0_replay": []}
    changed_counts = {arm: 0 for arm in ARMS}
    protected_total = 0

    for qid, question in questions.items():
        cand = candidates[qid]
        user_id = cand["user_id"]
        query = question["question"]
        options = question.get("options") or None
        base_rows = cand.get("data") or []

        c0 = v1_mod.ground_evidence(user_id, query, base_rows, 100, store=store)
        source_ids = []
        for row in c0:
            for sid in row.get("source_ids") or []:
                source_ids.append(str(sid))
        turns = read_turns(index, user_id, source_ids)

        arm_rows = {"c0": c0}
        arm_traces = {"c0": []}
        for arm in ("c1", "c2", "c3"):
            rows_out, trace, _changed = arm_mods[arm].annotate([dict(r) for r in c0], turns, query)
            arm_rows[arm] = rows_out
            arm_traces[arm] = trace

        hashes = {arm: hashlib.sha256(canonical_public(rows).encode()).hexdigest() for arm, rows in arm_rows.items()}
        cat = str(question.get("category"))
        protected = cat != "2" and qid not in PROTECTED_SINGLE_HOP
        if protected:
            protected_total += 1

        for arm in ("c1", "c2", "c3"):
            if hashes[arm] != hashes["c0"]:
                changed_counts[arm] += 1
                if protected:
                    failures["protected_changed"].append({"question_id": qid, "arm": arm})

        for arm in ("c1", "c2", "c3"):
            for entry in arm_traces[arm]:
                if entry.get("status") != "resolved":
                    continue
                value, precision = entry.get("value"), entry.get("precision")
                if precision == "day" and not (isinstance(value, str) and len(value) == 10):
                    failures["precision_violation"].append({"question_id": qid, "arm": arm, "value": value, "precision": precision})
                if precision == "month" and not (isinstance(value, str) and len(value) == 7):
                    failures["precision_violation"].append({"question_id": qid, "arm": arm, "value": value, "precision": precision})
                if precision == "year" and not (isinstance(value, str) and len(value) == 4):
                    failures["precision_violation"].append({"question_id": qid, "arm": arm, "value": value, "precision": precision})
                if entry.get("source_id") not in turns:
                    failures["cross_user"].append({"question_id": qid, "arm": arm, "source_id": entry.get("source_id")})

        # span verification for C3
        for entry in arm_traces["c3"]:
            sid = entry.get("source_id")
            turn = turns.get(sid)
            if turn is None:
                continue
            start, end = entry["span"]
            if turn["content"][start:end] != entry["raw_phrase"]:
                failures["span_mismatch"].append({"question_id": qid, "source_id": sid, "span": [start, end]})

        for arm in ARMS:
            kept = included_indices(arm_rows[arm], query, options)
            if len(kept) < len(included_indices(c0, query, options)):
                failures["budget_drop"].append({"question_id": qid, "arm": arm})

        ev_q = EvalQuestion(question_id=qid, question=query, gold=question.get("gold") or "",
                            category=question.get("category"), options=options)
        memories = {arm: format_memories(arm_rows[arm], question=query, options=options) for arm in ARMS}
        prompts = {arm: answer_prompt(question.get("dataset_id") or "locomo_refined", ev_q, memories[arm]) for arm in ARMS}

        diff_rows.append({
            "question_id": qid,
            "category": cat,
            "user_id": user_id,
            "protected": protected,
            "rows": len(c0),
            "sources": len(source_ids),
            "turns_resolved": len(turns),
            "hashes": hashes,
            "changed": {arm: hashes[arm] != hashes["c0"] for arm in ("c1", "c2", "c3")},
            "memories_hash": {arm: hashlib.sha256(memories[arm].encode()).hexdigest() for arm in ARMS},
            "prompt_hash": {arm: hashlib.sha256(prompts[arm].encode()).hexdigest() for arm in ARMS},
            "annotations": {arm: sum(1 for e in arm_traces[arm] if e.get("status") == "resolved") for arm in ("c1", "c2", "c3")},
        })
        for entry in arm_traces["c3"]:
            traces.append({"question_id": qid, **entry})

    replay = verify_smoke_replay(v1_mod, arm_mods, store, index, questions)
    failures["c0_replay"] = replay["c0_failures"]
    failures["c1_replay"] = replay["c1_failures"]

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "smoke_replay": replay["stats"],
        "mode": "offline",
        "questions": len(diff_rows),
        "protected_questions": protected_total,
        "changed_counts": changed_counts,
        "failures": {k: len(v) for k, v in failures.items()},
        "failure_samples": {k: v[:10] for k, v in failures.items()},
        "passed": all(len(v) == 0 for v in failures.values()),
    }
    write_json(out_dir / "input_diff.json", {"summary": summary, "questions": diff_rows})
    with (out_dir / "temporal_trace.jsonl").open("w", encoding="utf-8") as fh:
        for item in traces:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False)[:800])
    return 0 if summary["passed"] else 2


def verify_smoke_replay(v1_mod, arm_mods, store, index, questions) -> dict:
    """Replay the 300-question smoke set from its own candidates and compare artifacts."""
    smoke = Path(r"F:\codex数据库memory\locomo_smoke300_20261022")
    fresh = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\fresh")
    base = {r["question_id"]: r for r in json.loads((smoke / "baseline/aml_search.json").read_text(encoding="utf-8"))["results"]}
    saved_v1 = {r["question_id"]: r for r in json.loads((smoke / "variant/aml_search.json").read_text(encoding="utf-8"))["results"]}
    saved_v2 = {r["question_id"]: r for r in json.loads((fresh / "v2/aml_search.json").read_text(encoding="utf-8"))["results"]}

    c0_failures, c1_failures = [], []
    for qid, saved in saved_v1.items():
        question = questions.get(qid)
        if question is None:
            continue
        row = base[qid]
        user_id = row["user_id"]
        query = question["question"]
        rows = v1_mod.ground_evidence(user_id, query, row.get("data") or [], 100, store=store)
        if canonical_public(rows) != canonical_public(saved["data"]):
            c0_failures.append(qid)
            continue
        ids = [str(sid) for r in rows for sid in (r.get("source_ids") or [])]
        turns = read_turns(index, user_id, ids)
        c1_rows, _trace, _changed = arm_mods["c1"].annotate([dict(r) for r in rows], turns, query)
        expected = saved_v2.get(qid, {}).get("data")
        if expected is not None and canonical_public(c1_rows) != canonical_public(expected):
            c1_failures.append(qid)
    return {
        "stats": {"checked": len(saved_v1), "c0_failures": len(c0_failures), "c1_failures": len(c1_failures)},
        "c0_failures": c0_failures,
        "c1_failures": c1_failures,
    }
