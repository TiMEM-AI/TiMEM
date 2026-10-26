"""Full LoCoMo annotation experiment runner (prepare / offline / evaluate / report).

Implements the four-arm design from EXECUTION_PLAN.md:
  C0 raw_v1            original evidence, no annotations
  C1 a0_frozen         frozen A0 implementation
  C2 spec_format       frozen A0 parser, spec field format
  C3 spec_safe_context spec fields + parser fixes + source-sentence linkage
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import random
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

RUN_ROOT = Path(r"F:\codex数据库memory\locomo_temporal_annotation_full_20261023")
BENCH = Path(r"F:\worktable\research\bench")
BACKEND = Path(r"F:\worktable\research\timem-aml")
FULL_QUESTIONS = BENCH / "reports/f1022b/aml_questions.json"
FULL_CANDIDATES = BENCH / "reports/f1022b/aml_search.json"
SOURCE_SNAPSHOT = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\frozen\aml_sources.sqlite")
V1_FROZEN = Path(r"F:\codex数据库memory\locomo_smoke300_20261022\source_evidence.py")
A0_FROZEN = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023\patch\temporal_evidence.py")
SPEC = Path(r"F:\worktable\doc\temporal-annotation-spec.md")
ARMS_DIR = RUN_ROOT / "arms"

EXPECTED_COUNTS = {"2": 167, "1": 77, "4": 581, "3": 36}
PROTECTED_SINGLE_HOP = {"conv-47#q0088", "conv-48#q0080"}
ARMS = ("c0", "c1", "c2", "c3")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


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


# --------------------------------------------------------------- source db ---

class ReadOnlyIndex:
    def __init__(self, path: Path):
        self.path = str(path)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_turns(index: ReadOnlyIndex, user_id: str, source_ids) -> dict:
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


# ------------------------------------------------------------------ prepare ---

def run_prepare(args) -> int:
    root = Path(args.output)
    frozen = root / "frozen"
    frozen.mkdir(parents=True, exist_ok=True)

    questions_doc = read_json(FULL_QUESTIONS)
    candidates_doc = read_json(FULL_CANDIDATES)
    questions = questions_doc["questions"]
    candidates = candidates_doc["results"]
    q_by_id = {q["question_id"]: q for q in questions}
    c_by_id = {c["question_id"]: c for c in candidates}

    problems = []
    if len(questions) != 861 or len(q_by_id) != 861:
        problems.append(f"questions not 861 unique: {len(questions)}/{len(q_by_id)}")
    if len(candidates) != 861 or len(c_by_id) != 861:
        problems.append(f"candidates not 861 unique: {len(candidates)}/{len(c_by_id)}")
    if set(q_by_id) != set(c_by_id):
        problems.append("question/candidate qid sets differ")

    counts = {}
    for q in questions:
        counts[str(q.get("category"))] = counts.get(str(q.get("category")), 0) + 1
    if counts != EXPECTED_COUNTS:
        problems.append(f"category counts {counts} != {EXPECTED_COUNTS}")

    missing_user = [qid for qid, row in c_by_id.items() if not row.get("user_id")]
    if missing_user:
        problems.append(f"candidates missing user_id: {len(missing_user)}")

    index = ReadOnlyIndex(SOURCE_SNAPSHOT)
    with index.connect() as db:
        turns_n = db.execute("SELECT COUNT(*) FROM source_turns").fetchone()[0]
        requests_n = db.execute("SELECT COUNT(*) FROM source_requests").fetchone()[0]
    if (turns_n, requests_n) != (5882, 299):
        problems.append(f"source index counts {(turns_n, requests_n)} != (5882, 299)")

    if problems:
        for item in problems:
            print("PREPARE FAIL:", item)
        return 2

    # freeze inputs and code
    (frozen / "questions.json").write_bytes(FULL_QUESTIONS.read_bytes())
    (frozen / "candidates.json").write_bytes(FULL_CANDIDATES.read_bytes())
    for name, src in (("v1_source_evidence.py", V1_FROZEN), ("a0_temporal_evidence.py", A0_FROZEN)):
        (frozen / name).write_bytes(src.read_bytes())
    for name in ("arm_c1.py", "arm_c2.py", "arm_c3.py"):
        (frozen / name).write_bytes((ARMS_DIR / name).read_bytes())

    provenance = {
        "generated_at": utc_now(),
        "mode": "prepare",
        "counts": {"questions": len(questions), "candidates": len(candidates), "categories": counts,
                   "source_turns": turns_n, "source_requests": requests_n},
        "hashes": {
            "spec": sha256_file(SPEC),
            "questions": sha256_file(FULL_QUESTIONS),
            "candidates": sha256_file(FULL_CANDIDATES),
            "source_snapshot": sha256_file(SOURCE_SNAPSHOT),
            "v1_source_evidence": sha256_file(V1_FROZEN),
            "a0_temporal_evidence": sha256_file(A0_FROZEN),
            "bench_aml_score": sha256_file(BENCH / "evaluators/aml_score.py"),
            "bench_aml_run": sha256_file(BENCH / "runners/aml_run.py"),
            "arms": {name: sha256_file(ARMS_DIR / name) for name in ("arm_c1.py", "arm_c2.py", "arm_c3.py")},
        },
        "protected_single_hop": sorted(PROTECTED_SINGLE_HOP),
    }
    write_json(root / "preflight.json", provenance)

    from utils_paths import bench_model_config  # local helper
    model = bench_model_config()
    experiment = {
        "created_at": utc_now(),
        "run_root": str(root),
        "questions": str(frozen / "questions.json"),
        "candidates": str(frozen / "candidates.json"),
        "source_snapshot": str(SOURCE_SNAPSHOT),
        "arms": {
            "c0": {"label": "raw_v1", "module": None},
            "c1": {"label": "a0_frozen", "module": str(frozen / "arm_c1.py")},
            "c2": {"label": "spec_format", "module": str(frozen / "arm_c2.py")},
            "c3": {"label": "spec_safe_context", "module": str(frozen / "arm_c3.py")},
        },
        "denominator": 861,
        "primary_comparison": "c3-c0",
        "secondary_comparisons": ["c2-c1", "c3-c2"],
        "model": model,
        "temperature": 0,
        "seed": 20261023,
        "cache_protocol": "share one sample per identical full answer request and per identical judge request within a replicate",
        "budget_chars": 8000,
        "max_source_sentence_chars": 400,
        "hashes": provenance["hashes"],
        "counts": provenance["counts"],
    }
    write_json(root / "experiment.json", experiment)
    print(json.dumps({"prepare": "ok", "questions": len(questions), "categories": counts,
                      "model": model.get("answer_model")}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["prepare", "offline", "evaluate", "report"])
    parser.add_argument("--output", default=str(RUN_ROOT))
    parser.add_argument("--config", default=str(RUN_ROOT / "experiment.json"))
    parser.add_argument("--replicate", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.mode == "prepare":
        return run_prepare(args)
    if args.mode == "offline":
        from modes_offline import run_offline
        return run_offline(args)
    if args.mode == "evaluate":
        from modes_evaluate import run_evaluate
        return asyncio.run(run_evaluate(args))
    from modes_report import run_report
    return run_report(args)


if __name__ == "__main__":
    import asyncio
    raise SystemExit(main())
