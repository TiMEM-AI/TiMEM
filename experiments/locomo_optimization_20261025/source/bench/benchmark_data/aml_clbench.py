"""CLBench 解析。一题一条样本，分数看全部评分点是否都通过。"""

from __future__ import annotations

from typing import Any

from benchmark_data.aml_models import EvalMessage, EvalQuestion, EvalSample


def clbench_row_to_sample(row: dict[str, Any]) -> EvalSample:
    metadata = row.get("metadata") or {}
    task_id = str(metadata.get("task_id") or row.get("task_id") or "")
    messages = _memory_messages(row.get("messages") or [])
    question = _question_text(row.get("messages") or [])
    rubrics = [str(item).strip() for item in (row.get("rubrics") or []) if str(item).strip()]
    return EvalSample(
        dataset_id="clbench",
        sample_id=task_id,
        messages=messages,
        questions=[
            EvalQuestion(
                question_id=task_id,
                question=question,
                gold="\n".join(rubrics),
                category=str(metadata.get("category") or "clbench"),
                metadata={
                    "rubrics": rubrics,
                    "context_id": metadata.get("context_id"),
                    "task_id": task_id,
                },
            )
        ],
    )


def _memory_messages(raw: list[dict[str, Any]]) -> list[EvalMessage]:
    turns = [item for item in raw if isinstance(item, dict) and str(item.get("content") or "").strip()]
    if len(turns) > 1 and str(turns[-1].get("role") or "").lower() == "user":
        turns = turns[:-1]
    messages: list[EvalMessage] = []
    for item in turns:
        role = str(item.get("role") or "user").lower()
        if role not in {"user", "assistant"}:
            role = "user"
        messages.append(EvalMessage(role=role, content=str(item["content"]).strip()))
    return messages


def _question_text(raw: list[dict[str, Any]]) -> str:
    users = [
        str(item.get("content") or "").strip()
        for item in raw
        if isinstance(item, dict) and str(item.get("role") or "").lower() == "user"
    ]
    return users[-1] if users else ""


def rubric_prompt(rubrics: list[str], answer: str) -> str:
    numbered = "\n".join(f"{index}. {text}" for index, text in enumerate(rubrics, start=1))
    return (
        "Judge the answer against every rubric. Reply with one line per rubric, "
        "exactly 'yes' or 'no', in the same order. Use yes only when that rubric is fully met.\n"
        f"Rubrics:\n{numbered}\n\nAnswer:\n{answer}"
    )


def all_rubrics_passed(judged: str, rubric_count: int) -> bool:
    lines = [line.strip().lower() for line in judged.splitlines() if line.strip()]
    verdicts = [line for line in lines if line in {"yes", "no"}]
    if rubric_count <= 0 or len(verdicts) < rubric_count:
        return False
    return all(item == "yes" for item in verdicts[:rubric_count])
