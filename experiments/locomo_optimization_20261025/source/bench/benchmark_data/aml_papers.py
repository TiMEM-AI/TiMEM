"""Load aml-eval paper JSON and filter EvalSample lists."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmark_data.aml_models import EvalQuestion, EvalSample
from benchmark_data.catalog import get_dataset
from utils.config import PROJECT_ROOT


def resolve_paper_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_file():
        return path.resolve()
    for base in (PROJECT_ROOT, PROJECT_ROOT.parent):
        candidate = (base / raw).resolve()
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Paper not found: {raw}")


def load_paper(raw: str | Path) -> dict[str, Any]:
    path = resolve_paper_path(str(raw)) if not isinstance(raw, Path) else raw
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload.get("dataset_id"):
        raise ValueError(f"Invalid paper JSON (need dataset_id): {path}")
    return payload


def ensure_runnable_dataset(dataset_id: str, paper: dict[str, Any] | None) -> None:
    entry = get_dataset(dataset_id)
    if entry.runnable:
        return
    tier = (paper or {}).get("tier") or "?"
    raise ValueError(
        f"{dataset_id} is not runnable yet ({entry.note}). "
        f"Paper tier={tier} is defined in aml-eval but Add/Search needs a loader first."
    )


def apply_paper(samples: list[EvalSample], paper: dict[str, Any]) -> list[EvalSample]:
    if paper.get("scope") == "all" or paper.get("tier") == "full":
        if not paper.get("sample_ids") and not paper.get("question_ids") and paper.get("csv_row_indexes") is None:
            return samples

    if paper.get("csv_row_indexes") is not None:
        indexes = sorted({int(i) for i in paper["csv_row_indexes"]})
        picked = [samples[i] for i in indexes if 0 <= i < len(samples)]
        return picked

    sample_ids = paper.get("sample_ids")
    if sample_ids:
        allowed = {str(s) for s in sample_ids}
        samples = [s for s in samples if s.sample_id in allowed]

    question_ids = paper.get("question_ids")
    if question_ids:
        allowed_q = {str(q) for q in question_ids}
        filtered: list[EvalSample] = []
        for sample in samples:
            questions = [q for q in sample.questions if q.question_id in allowed_q]
            if questions:
                filtered.append(
                    EvalSample(
                        dataset_id=sample.dataset_id,
                        sample_id=sample.sample_id,
                        messages=sample.messages,
                        questions=questions,
                    )
                )
        samples = filtered

    task_ids = paper.get("task_ids")
    if task_ids:
        allowed_t = {str(t) for t in task_ids}
        kept: list[EvalSample] = []
        for sample in samples:
            questions = [q for q in sample.questions if q.question_id in allowed_t]
            if questions:
                kept.append(
                    EvalSample(
                        dataset_id=sample.dataset_id,
                        sample_id=sample.sample_id,
                        messages=sample.messages,
                        questions=questions,
                    )
                )
        samples = kept

    return samples
