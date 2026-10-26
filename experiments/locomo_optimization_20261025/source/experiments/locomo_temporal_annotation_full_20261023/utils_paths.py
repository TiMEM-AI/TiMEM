"""Small shared helpers for the annotation experiment (non-secret config only)."""
from __future__ import annotations

import sys
from urllib.parse import urlparse

BENCH = r"F:\worktable\research\bench"


def bench_model_config() -> dict:
    if BENCH not in sys.path:
        sys.path.insert(0, BENCH)
    from runners.aml_run import _score_endpoints  # type: ignore

    endpoints = _score_endpoints()
    return {
        "answer_model": endpoints.get("answer_model"),
        "judge_model": endpoints.get("judge_model"),
        "answer_host": urlparse(endpoints.get("answer_base") or "").netloc,
        "judge_host": urlparse(endpoints.get("judge_base") or "").netloc,
    }
