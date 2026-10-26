"""Load the runnable AML text datasets into EvalSample objects."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import logging
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from benchmark_data.aml_models import EvalMessage, EvalQuestion, EvalSample
from benchmark_data.catalog import get_dataset
from utils.config import PROJECT_ROOT
from utils.locomo_dates import parse_locomo_datetime

logger = logging.getLogger(__name__)

CACHE_DIR = PROJECT_ROOT / "benchmark_data" / "cache"

LOCOMO_REFINED_URL = (
    "https://raw.githubusercontent.com/mem-eval-suite/LoCoMo_refined/main/data/raw/locomo_refined.json"
)
LONGMEMEVAL_S_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json"
)
PERSONAMEM_CSV_URL = (
    "https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2/resolve/main/benchmark/text/benchmark.csv"
)
PERSONAMEM_FILE_ROOT = "https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2/resolve/main/"

_RECALL_SUFFIX = (
    " Please recall my related preferences from our conversation history "
    "to give personalized responses."
)


def load_samples(
    dataset_id: str,
    *,
    limit: int | None = None,
    paper: dict[str, Any] | None = None,
) -> list[EvalSample]:
    entry = get_dataset(dataset_id)
    if not entry.runnable:
        raise ValueError(f"{dataset_id} is registered only: {entry.note}")
    if dataset_id == "locomo_refined":
        samples = _load_locomo_refined()
    elif dataset_id == "longmemeval_s":
        samples = _load_longmemeval_s()
    elif dataset_id == "personamem_v2_32k":
        samples = _load_personamem("32k")
    elif dataset_id == "personamem_v2_128k":
        samples = _load_personamem("128k")
    elif dataset_id == "beam":
        samples = _load_beam(paper)
    elif dataset_id == "clbench":
        samples = _load_clbench()
    else:
        raise ValueError(f"No loader for {dataset_id}")
    if paper is not None:
        from benchmark_data.aml_papers import apply_paper

        samples = apply_paper(samples, paper)
    elif limit is not None:
        return samples[:limit]
    return samples


def _load_beam(paper: dict[str, Any] | None) -> list[EvalSample]:
    from datasets import load_dataset

    from benchmark_data.aml_beam import BEAM_SPLITS, beam_row_to_sample

    split = str((paper or {}).get("split") or "100K")
    if split not in BEAM_SPLITS:
        raise ValueError(f"BEAM split must be one of {', '.join(BEAM_SPLITS)}")
    rows = load_dataset("Mohammadta/BEAM", split=split)
    return [beam_row_to_sample(dict(row), split=split) for row in rows]


def _load_clbench() -> list[EvalSample]:
    from benchmark_data.aml_clbench import clbench_row_to_sample

    path = CACHE_DIR / "CL-bench.jsonl"
    if not path.exists():
        logger.info("Downloading CL-bench.jsonl")
        response = httpx.get(
            "https://huggingface.co/datasets/tencent/CL-bench/resolve/main/CL-bench.jsonl",
            timeout=180.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    samples: list[EvalSample] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                samples.append(clbench_row_to_sample(json.loads(text)))
    return samples


def _cache_json(name: str, url: str) -> Any:
    path = CACHE_DIR / name
    if path.exists():
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    logger.info("Downloading %s", url)
    response = httpx.get(url, timeout=180.0, follow_redirects=True)
    response.raise_for_status()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return response.json()


def _load_locomo_refined() -> list[EvalSample]:
    rows = _cache_json("locomo_refined.json", LOCOMO_REFINED_URL)
    samples: list[EvalSample] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        sample_id = str(row.get("sample_id") or f"conv-{index}")
        conversation = row.get("conversation") or {}
        messages = _locomo_messages(conversation if isinstance(conversation, dict) else {})
        questions: list[EvalQuestion] = []
        for qidx, qa in enumerate(row.get("qa") or []):
            if not isinstance(qa, dict) or qa.get("is_multi_modality"):
                continue
            question = str(qa.get("question") or "").strip()
            gold = _gold_text(qa.get("answer"))
            if not question or not gold:
                continue
            questions.append(
                EvalQuestion(
                    question_id=f"{sample_id}#q{qidx:04d}",
                    question=question,
                    gold=gold,
                    category=str(qa.get("category") or "") or None,
                )
            )
        if messages and questions:
            samples.append(
                EvalSample(
                    dataset_id="locomo_refined",
                    sample_id=sample_id,
                    messages=messages,
                    questions=questions,
                )
            )
    return samples


def _locomo_messages(conversation: dict[str, Any]) -> list[EvalMessage]:
    speaker_map = _speaker_roles(conversation)
    session_nums = sorted(
        int(match.group(1))
        for key in conversation
        if (match := re.match(r"session_(\d+)$", str(key)))
    )
    messages: list[EvalMessage] = []
    for num in session_nums:
        turns = conversation.get(f"session_{num}") or []
        timestamp_ms = _iso_to_ms(parse_locomo_datetime(str(conversation.get(f"session_{num}_date_time") or "")))
        if not isinstance(turns, list):
            continue
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            content = str(turn.get("text") or turn.get("content") or "").strip()
            if not content:
                continue
            speaker = str(turn.get("speaker") or turn.get("role") or "").strip()
            role = speaker_map.get(speaker.casefold(), "user")
            if role not in {"user", "assistant"}:
                continue
            messages.append(EvalMessage(role=role, content=content, timestamp_ms=timestamp_ms))
    return messages


def _speaker_roles(conversation: dict[str, Any]) -> dict[str, str]:
    names: list[str] = []
    seen: set[str] = set()
    for key, turns in conversation.items():
        if not re.match(r"session_\d+$", str(key)) or not isinstance(turns, list):
            continue
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            name = str(turn.get("speaker") or "").strip()
            if not name:
                continue
            folded = name.casefold()
            if folded not in seen:
                seen.add(folded)
                names.append(name)
    names.sort(key=str.casefold)
    mapping: dict[str, str] = {}
    for index, name in enumerate(names):
        mapping[name.casefold()] = "user" if index == 0 else "assistant"
    return mapping


def _load_longmemeval_s() -> list[EvalSample]:
    rows = _cache_json("longmemeval_s_cleaned.json", LONGMEMEVAL_S_URL)
    samples: list[EvalSample] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        sample_id = str(row.get("question_id") or index)
        messages = _longmemeval_messages(row)
        question = str(row.get("question") or "").strip()
        gold = _gold_text(row.get("answer"))
        if not messages or not question:
            continue
        samples.append(
            EvalSample(
                dataset_id="longmemeval_s",
                sample_id=sample_id,
                messages=messages,
                questions=[
                    EvalQuestion(
                        question_id=sample_id,
                        question=question,
                        gold=gold,
                        category=str(row.get("question_type") or "") or None,
                        metadata={"abstention": sample_id.endswith("_abs")},
                    )
                ],
            )
        )
    return samples


def _longmemeval_messages(row: dict[str, Any]) -> list[EvalMessage]:
    sessions = row.get("haystack_sessions") or []
    dates = row.get("haystack_dates") or []
    messages: list[EvalMessage] = []
    for index, session in enumerate(sessions):
        timestamp_ms = _loose_time_to_ms(dates[index]) if index < len(dates) else None
        if not isinstance(session, list):
            continue
        for turn in session:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = str(turn.get("content") or "").strip()
            if content:
                messages.append(EvalMessage(role=role, content=content, timestamp_ms=timestamp_ms))
    return messages


def _load_personamem(size: str) -> list[EvalSample]:
    dataset_id = f"personamem_v2_{size}"
    csv_name = "personamem_v2_benchmark.csv"
    csv_path = CACHE_DIR / csv_name
    if not csv_path.exists():
        logger.info("Downloading %s", PERSONAMEM_CSV_URL)
        response = httpx.get(PERSONAMEM_CSV_URL, timeout=180.0, follow_redirects=True)
        response.raise_for_status()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        csv_path.write_bytes(response.content)
    samples: list[EvalSample] = []
    with open(csv_path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            link_key = f"chat_history_{size}_link"
            link = str(row.get(link_key) or "").strip()
            persona_id = str(row.get("persona_id") or len(samples))
            history = _personamem_history(link)
            messages = [
                EvalMessage(role=item["role"], content=item["content"])
                for item in history
                if item["role"] in {"user", "assistant"} and item["content"]
            ]
            question = str(row.get("user_query") or "").strip()
            correct = str(row.get("correct_answer") or "").strip()
            if not messages or not question or not correct:
                continue
            options = _personamem_options(persona_id, question, correct, row.get("incorrect_answers"))
            samples.append(
                EvalSample(
                    dataset_id=dataset_id,
                    sample_id=f"persona-{persona_id}",
                    messages=messages,
                    questions=[
                        EvalQuestion(
                            question_id=f"persona-{persona_id}",
                            question=question,
                            gold=correct,
                            category=str(row.get("pref_type") or "") or None,
                            options=options,
                            preference=str(row.get("preference") or "").strip() or None,
                        )
                    ],
                )
            )
    return samples


def _personamem_history(link: str) -> list[dict[str, str]]:
    safe_name = link.replace("/", "__")
    path = CACHE_DIR / "personamem" / safe_name
    if not path.exists():
        url = PERSONAMEM_FILE_ROOT + link.lstrip("/")
        logger.info("Downloading %s", url)
        response = httpx.get(url, timeout=180.0, follow_redirects=True)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    payload = json.loads(path.read_text(encoding="utf-8"))
    history = payload.get("chat_history") if isinstance(payload, dict) else payload
    if not isinstance(history, list):
        return []
    messages: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        messages.append(
            {
                "role": str(item.get("role") or "").strip().lower(),
                "content": str(item.get("content") or "").strip(),
            }
        )
    return messages


def _personamem_options(
    persona_id: str,
    question: str,
    correct: str,
    incorrect_raw: Any,
) -> list[str]:
    incorrect = _parse_list(incorrect_raw)
    options = [correct] + [str(item) for item in incorrect if str(item).strip()]
    seed_text = f"{persona_id}_{question}{_RECALL_SUFFIX}"
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:8], 16)
    random.Random(seed).shuffle(options)
    return [f"{chr(65 + index)}. {text}" for index, text in enumerate(options)]


def _parse_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return [text]
    return list(parsed) if isinstance(parsed, list) else [text]


def _gold_text(value: Any) -> str:
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " / ".join(parts)
    return str(value or "").strip()


def _iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return None


_EN_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _english_month_day(text: str) -> int | None:
    """BEAM time anchors look like March-15-2024. Month names stay English."""
    match = re.fullmatch(r"([A-Za-z]+)-(\d{1,2})-(\d{4})", text)
    if not match:
        return None
    month = _EN_MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    try:
        return int(datetime(int(match.group(3)), month, int(match.group(2))).timestamp() * 1000)
    except ValueError:
        return None


def _loose_time_to_ms(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        return number if number > 10_000_000_000 else number * 1000
    month_day = _english_month_day(text)
    if month_day is not None:
        return month_day
    for fmt in (
        "%Y/%m/%d (%a) %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return int(datetime.strptime(text, fmt).timestamp() * 1000)
        except ValueError:
            continue
    iso = parse_locomo_datetime(text)
    return _iso_to_ms(iso) if iso else None
