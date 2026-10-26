"""A0 temporal ablation harness (preflight / offline / changed / fresh-paired).

Designed for the handoff document
`F:\\codex数据库memory\\temporal_handoff_20261022\\IMPLEMENTATION_PLAN.md`.

Modes
-----
preflight      consistent snapshot of the source index + hashes, no model calls
offline        replay 300 frozen questions, compare V1 vs V2 inputs, no model calls
changed        score only questions whose request hash changed (reuse saved V1 rows)
fresh-paired   score all 300 questions for both arms with fresh model calls

Only the preflight mode is implemented in this first pass; the remaining modes
are added once the preflight report is green.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(r"F:\worktable\research\timem-aml")
BENCH = Path(r"F:\worktable\research\bench")
OUT = Path(r"F:\codex数据库memory\locomo_temporal_v2_20261023")
FROZEN = Path(r"F:\codex数据库memory\locomo_smoke300_20261022")

EXPECTED = {
    "source_evidence_frozen": "f9d9ca1532266d1302e28139433fd6ad2d5693693d072787375e28c10a6f4835",
    "source_index_active": "cf56a1cf814eca61d5ec30b3a9722ddd4c8d6bd5a85c6868ebaab10477ad5888",
    "manifest": "b99c43721dafec8321ba1ccd95d5201a6bc853f97de1af69bbf111c6f183d92b",
    "core_content": "21ee7dbc4ea43acf320c547e102bb84df4fff2cb2b43d1dd0af4f6668097834d",
}

CODE_FILES = [
    "server/app.py",
    "server/engine.py",
    "server/search_expand.py",
    "server/time_anchor.py",
    "server/source_evidence.py",
    "server/ownership.py",
    "server/l2_backfill.py",
    "config/settings.yaml",
    "config/prompts.yaml",
    "config/datasets/default/retrieval_config.yaml",
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def snapshot_sqlite(source: Path, target: Path) -> None:
    """Consistent copy through the SQLite backup API (WAL safe)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise SystemExit(f"snapshot already exists, refusing to overwrite: {target}")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def logical_index_hashes(db_path: Path) -> tuple[str, str, int, int]:
    """Ordered content hashes so physical file bytes do not matter."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        turns = con.execute(
            "SELECT id,user_id,session_id,request_id,position,observed_at,role,content "
            "FROM source_turns ORDER BY id"
        ).fetchall()
        requests = con.execute(
            "SELECT user_id,request_id,digest FROM source_requests ORDER BY user_id,request_id"
        ).fetchall()
    finally:
        con.close()
    turns_payload = [list(r) for r in turns]
    requests_payload = [list(r) for r in requests]
    return (
        sha256_text(json.dumps(turns_payload, ensure_ascii=False, sort_keys=True)),
        sha256_text(json.dumps(requests_payload, ensure_ascii=False, sort_keys=True)),
        len(turns_payload),
        len(requests_payload),
    )


def model_config() -> dict:
    """Non-secret model settings actually used by the answer/judge calls."""
    sys.path.insert(0, str(BENCH))
    from utils.config import get_settings  # type: ignore

    settings = get_settings()
    return {
        "answer_model": settings.answer_model or settings.judge_model,
        "judge_model": settings.judge_model,
        "answer_base_host": _host(settings.answer_api_base or settings.ark_api_base),
        "judge_base_host": _host(settings.judge_api_base or settings.ark_api_base),
        "judge_temperature": getattr(settings, "judge_temperature", None),
    }


def _host(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return parsed.netloc or None


def run_preflight(args) -> int:
    frozen_run = Path(args.frozen_run)
    source_db = Path(args.source_db)
    output = Path(args.output)
    snapshot = output / "aml_sources.sqlite"

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tools").mkdir(parents=True, exist_ok=True)

    if not source_db.exists():
        raise SystemExit(f"source db missing: {source_db}")

    if snapshot.exists() and args.reuse_snapshot:
        print(f'reusing existing snapshot: {snapshot}')
    else:
        snapshot_sqlite(source_db, snapshot)
    turns_hash, requests_hash, turns_n, requests_n = logical_index_hashes(snapshot)

    code_hashes = {name: sha256_file(BACKEND / name) for name in CODE_FILES if (BACKEND / name).exists()}
    report = {
        "generated_at": utc_now(),
        "mode": "preflight",
        "paths": {
            "backend": str(BACKEND),
            "benchmark": str(BENCH),
            "frozen_run": str(frozen_run),
            "active_source_db": str(source_db),
            "snapshot": str(snapshot),
            "output": str(output),
        },
        "hashes": {
            "source_evidence_frozen": sha256_file(frozen_run / "source_evidence.py"),
            "source_evidence_backend": sha256_file(BACKEND / "server/source_evidence.py"),
            "source_index_active": sha256_file(source_db),
            "source_index_snapshot": sha256_file(snapshot),
            "manifest": sha256_file(frozen_run / "manifest.json"),
            "code": code_hashes,
        },
        "logical_hashes": {
            "source_turns": turns_hash,
            "source_requests": requests_hash,
        },
        "counts": {
            "source_turns": turns_n,
            "source_requests": requests_n,
        },
        "model": model_config(),
        "checks": {
            "source_evidence_backend_matches_frozen": (
                sha256_file(BACKEND / "server/source_evidence.py")
                == sha256_file(frozen_run / "source_evidence.py")
                == EXPECTED["source_evidence_frozen"]
            ),
            "manifest_matches_expected": sha256_file(frozen_run / "manifest.json") == EXPECTED["manifest"],
            "source_index_matches_expected": sha256_file(source_db) == EXPECTED["source_index_active"],
        },
        "notes": [
            "snapshot taken with the SQLite backup API; byte hash may differ from the active file",
            "core memory aggregate hash is recorded in the frozen audit.final.json and is not recomputed here",
            "no model calls were made in this mode",
        ],
    }
    write_json(output / "preflight.json", report)
    green = all(report["checks"].values())
    print(json.dumps({"mode": "preflight", "checks": report["checks"], "turns": turns_n, "requests": requests_n}, ensure_ascii=False))
    print(f"report: {output / 'preflight.json'}")
    return 0 if green else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["preflight", "offline", "changed", "fresh-paired"])
    parser.add_argument("--frozen-run", default=str(FROZEN))
    parser.add_argument("--source-db", default=str(BACKEND / "data/aml_sources.sqlite"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--feature", default="calendar_v1")
    parser.add_argument("--reuse-snapshot", action="store_true")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    if args.mode == "preflight":
        return run_preflight(args)
    if args.mode == "offline":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from offline_mode import run_offline
        return run_offline(args)
    if args.mode == "fresh-paired":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from fresh_mode import run_fresh
        import asyncio
        return asyncio.run(run_fresh(args))
    raise SystemExit(f"mode not implemented yet: {args.mode}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
