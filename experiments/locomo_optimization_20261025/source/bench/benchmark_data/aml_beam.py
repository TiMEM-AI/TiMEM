"""BEAM 公开档解析。10M 未公开，不在这里。"""

from __future__ import annotations

import ast
from typing import Any

from benchmark_data.aml_loaders import _loose_time_to_ms
from benchmark_data.aml_models import EvalMessage, EvalQuestion, EvalSample


BEAM_SPLITS = ("100K", "500K", "1M")


def beam_row_to_sample(row: dict[str, Any], *, split: str) -> EvalSample:
    sample_id = str(row.get("conversation_id") or "")
    messages = _chat_messages(row.get("chat"))
    questions = _probing_questions(row.get("probing_questions"), sample_id)
    return EvalSample(
        dataset_id="beam",
        sample_id=sample_id,
        messages=messages,
        questions=questions,
    )


def _chat_messages(chat: Any) -> list[EvalMessage]:
    messages: list[EvalMessage] = []
    for turn in _flatten(chat):
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        role = str(turn.get("role") or "user").lower()
        if role not in {"user", "assistant"}:
            role = "user"
        messages.append(
            EvalMessage(
                role=role,
                content=content,
                timestamp_ms=_loose_time_to_ms(turn.get("time_anchor")),
            )
        )
    return messages


def _flatten(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        return [node]
    if isinstance(node, list):
        found: list[dict[str, Any]] = []
        for item in node:
            found.extend(_flatten(item))
        return found
    return []


def _probing_questions(raw: Any, sample_id: str) -> list[EvalQuestion]:
    data = raw
    if isinstance(raw, str):
        data = ast.literal_eval(raw)
    groups: list[tuple[str, Any]] = []
    if isinstance(data, dict):
        groups = [(str(key), value) for key, value in data.items()]
    elif isinstance(data, list):
        groups = [("", data)]
    questions: list[EvalQuestion] = []
    for ability, value in groups:
        items = value if isinstance(value, list) else [value]
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            text = str(item.get("question") or "").strip()
            gold = str(item.get("ideal_response") or item.get("answer") or "").strip()
            if not text or not gold:
                continue
            kind = str(item.get("type") or ability or "beam")
            questions.append(
                EvalQuestion(
                    question_id=f"{sample_id}#{kind}#{index}",
                    question=text,
                    gold=gold,
                    category=kind,
                    metadata={"ability": ability, "difficulty": item.get("difficulty")},
                )
            )
    return questions
