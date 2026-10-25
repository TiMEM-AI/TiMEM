"""A0 offline audit: replay 300 frozen questions, compare V1 vs V2 inputs.

No model calls. Hard gate: for every non-temporal question the public rows,
internal source ids and formatted memories must be byte-identical between the
frozen V1 module and the new V2 module.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(r"F:\worktable\research\timem-aml")
BENCH = Path(r"F:\worktable\research\bench")
OUT = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023")
FROZEN = Path(r"F:\codex数据库memory\locomo_smoke300_20261022")
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def canonical_public(rows) -> str:
    payload = [
        {
            "id": str(row.get("id") or ""),
            "content": str(row.get("content") or ""),
            "score": row.get("score"),
            "created_at": row.get("created_at"),
        }
        for row in rows
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_readonly_store(module, path: Path, name: str):
    """Duck-typed store: read-only connection, real module search implementation."""

    class SnapshotStore:
        def __init__(self):
            self.path = str(path)
            self.__name__ = name

        @contextmanager
        def connect(self):
            db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                yield db
            finally:
                db.close()

        def search(self, user_id, query, limit=20):
            return module.SourceEvidenceStore.search(self, user_id, query, limit)

    return SnapshotStore()


def included_indices(records, question: str, options) -> list:
    """Replicate format_memories token accounting to see which records survive."""
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
        created_at = str(record.get("created_at") or "").strip()
        line = f"- [{created_at}] {content}" if created_at else f"- {content}"
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


def run_offline(args) -> int:
    frozen_run = Path(args.frozen_run)
    snapshot = Path(args.source_db)
    output = Path(args.output)
    feature = args.feature or "calendar_v1"

    if not snapshot.exists():
        raise SystemExit(f"snapshot missing: {snapshot} (run --mode preflight first)")

    manifest = json.loads((frozen_run / "manifest.json").read_text(encoding="utf-8"))
    questions = {q["question_id"]: q for q in manifest["questions"]}
    baseline = {r["question_id"]: r for r in json.loads((frozen_run / "baseline/aml_search.json").read_text(encoding="utf-8"))["results"]}
    frozen_v1 = {r["question_id"]: r for r in json.loads((frozen_run / "variant/aml_search.json").read_text(encoding="utf-8"))["results"]}

    frozen_mod = load_module(frozen_run / "source_evidence.py", "frozen_source_evidence")
    v2_mod = load_module(BACKEND / "server/source_evidence.py", "v2_source_evidence")
    v1_store = make_readonly_store(frozen_mod, snapshot, "v1")
    v2_store = make_readonly_store(v2_mod, snapshot, "v2")

    sys.path.insert(0, str(BENCH))
    from evaluators.aml_score import format_memories  # noqa: E402
    from benchmark_data.aml_models import EvalQuestion  # noqa: E402

    os.environ["TIMEM_SOURCE_EVIDENCE"] = "1"
    os.environ["TIMEM_TEMPORAL_RENDERER"] = "off"

    diff_rows = []
    traces = []
    protection_failures = []
    replay_failures = []
    temporal_changed = 0
    non_temporal_changed = 0
    budget_failures = []

    for qid, question in questions.items():
        hit = baseline.get(qid)
        if hit is None:
            continue
        base_rows = hit.get("data") or []
        user_id = hit.get("user_id")
        query = question["question"]
        dataset_id = question.get("dataset_id") or "locomo_refined"
        options = question.get("options") or None

        v1_rows = frozen_mod.ground_evidence(user_id, query, base_rows, 100, store=v1_store)

        os.environ["TIMEM_TEMPORAL_RENDERER"] = feature
        v2_rows = v2_mod.ground_evidence(user_id, query, base_rows, 100, store=v2_store)

        frozen_rows = frozen_v1.get(qid, {}).get("data") or []
        replay_ok = canonical_public(v1_rows) == canonical_public(frozen_rows)

        ev_q = EvalQuestion(
            question_id=qid, question=query, gold=question.get("gold") or "",
            category=question.get("category"), options=options,
        )
        mem_v1 = format_memories(v1_rows, question=query, options=options)
        mem_v2 = format_memories(v2_rows, question=query, options=options)
        from evaluators.aml_score import answer_prompt  # noqa: E402

        prompt_v1 = answer_prompt(dataset_id, ev_q, mem_v1)
        prompt_v2 = answer_prompt(dataset_id, ev_q, mem_v2)

        keep_v1 = included_indices(v1_rows, query, options)
        keep_v2 = included_indices(v2_rows, query, options)
        dropped = [i for i in keep_v1 if i not in keep_v2]

        public_same = canonical_public(v1_rows) == canonical_public(v2_rows)
        source_same = [r.get("source_ids") for r in v1_rows] == [r.get("source_ids") for r in v2_rows]
        level_same = [r.get("level") for r in v1_rows] == [r.get("level") for r in v2_rows]
        mem_same = mem_v1 == mem_v2
        prompt_same = prompt_v1 == prompt_v2

        category = str(question.get("category"))
        if not replay_ok:
            replay_failures.append(qid)

        changed = not public_same
        if changed:
            if category == "2":
                temporal_changed += 1
            else:
                non_temporal_changed += 1

        if category != "2":
            if not (public_same and source_same and level_same and mem_same and prompt_same):
                protection_failures.append(qid)
        elif not changed:
            if not (source_same and level_same and mem_same and prompt_same):
                protection_failures.append(qid)

        if dropped:
            budget_failures.append({"question_id": qid, "dropped_records": dropped})

        diff_rows.append({
            "question_id": qid,
            "category": category,
            "replay_ok": replay_ok,
            "changed": changed,
            "public_hash_v1": sha256_text(canonical_public(v1_rows)),
            "public_hash_v2": sha256_text(canonical_public(v2_rows)),
            "source_ids_same": source_same,
            "level_same": level_same,
            "memories_hash_v1": sha256_text(mem_v1),
            "memories_hash_v2": sha256_text(mem_v2),
            "prompt_hash_v1": sha256_text(prompt_v1),
            "prompt_hash_v2": sha256_text(prompt_v2),
            "rows_v1": len(v1_rows),
            "rows_v2": len(v2_rows),
            "kept_v1": len(keep_v1),
            "kept_v2": len(keep_v2),
        })

        if changed and category == "2":
            for row in v2_rows:
                content = str(row.get("content") or "")
                if "Calendar annotations derived from the original turns:" in content:
                    traces.append({"question_id": qid, "row_id": row.get("id"),
                                   "annotation_block": content.split("Calendar annotations derived from the original turns:", 1)[1].strip()})

    report = {
        "generated_at": utc_now(),
        "mode": "offline",
        "feature": feature,
        "questions": len(diff_rows),
        "replay_failures": replay_failures,
        "protection_failures": protection_failures,
        "budget_failures": budget_failures,
        "changed_temporal": temporal_changed,
        "changed_non_temporal": non_temporal_changed,
        "passed": not replay_failures and not protection_failures and not budget_failures,
    }
    write_json(output / "input_diff.json", {"summary": report, "questions": diff_rows})
    with (output / "temporal_trace.jsonl").open("w", encoding="utf-8") as fh:
        for item in traces:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    write_json(output / "summary.json", report)
    print(json.dumps({k: report[k] for k in ("questions", "changed_temporal", "changed_non_temporal", "passed")}, ensure_ascii=False))
    print(f"replay_failures={len(replay_failures)} protection_failures={len(protection_failures)} budget_failures={len(budget_failures)}")
    print(f"report: {output / 'input_diff.json'}")
    return 0 if report["passed"] else 2
