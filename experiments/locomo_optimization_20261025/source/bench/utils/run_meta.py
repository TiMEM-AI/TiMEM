"""Run metadata: which harness and backend code produced a report."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.config import PROJECT_ROOT, get_settings

ANSWER_TEMPERATURE = 0.0  # evaluators.aml_score.complete_chat sends temperature 0 for answer and judge.


def git_state(directory: str | Path | None) -> dict[str, Any]:
    """Commit, branch, and dirty flag of a git checkout. Never raises."""
    if not directory:
        return {}
    path = Path(directory)
    if not path.exists():
        return {"dir": str(path), "error": "missing"}

    def _git(*args: str) -> str:
        out = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace"
        )
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or f"git {' '.join(args)} failed")
        return out.stdout.strip()

    try:
        status = _git("status", "--porcelain", "--untracked-files=no")
        return {
            "dir": str(path),
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(status),
            "dirty_files": len(status.splitlines()) if status else 0,
        }
    except Exception as exc:  # not a repo, git missing, timeout
        return {"dir": str(path), "error": str(exc)[:200]}


def collect_run_meta(*, stage: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    manifest = manifest or {}
    backend_dir = os.getenv("AML_BACKEND_DIR", "").strip()
    backend: dict[str, Any] = git_state(backend_dir) if backend_dir else {}
    override = os.getenv("AML_BACKEND_COMMIT", "").strip()
    if override:
        backend["commit_declared"] = override
    note = os.getenv("AML_BACKEND_NOTE", "").strip()
    if note:
        backend["note"] = note
    return {
        "stage": stage,
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "time_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "benchmark": git_state(PROJECT_ROOT),
        "backend": backend,
        "endpoints": {
            "add_url": settings.aml_add_url,
            "search_url": settings.aml_search_url,
            "auth": settings.aml_auth,
        },
        "models": {
            "answer_model": settings.answer_model or settings.judge_model,
            "judge_model": settings.judge_model,
            "answer_temperature": ANSWER_TEMPERATURE,
            "judge_temperature": ANSWER_TEMPERATURE,
        },
        "dataset_id": manifest.get("dataset_id"),
        "paper_id": manifest.get("paper_id"),
        "paper_path": manifest.get("paper_path"),
        "questions": len(manifest.get("questions") or []),
    }
