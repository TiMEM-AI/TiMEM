"""Run the platform side of an AML text evaluation: Add, Search, then local score."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from adapters.aml_client import AmlClient
from benchmark_data.aml_contract import AML_HTTP_TIMEOUT_SEC, add_body, search_body
from benchmark_data.aml_loaders import load_samples
from benchmark_data.aml_models import EvalQuestion, EvalSample
from benchmark_data.chunking import chunk_messages
from evaluators.aml_score import (
    answer_prompt,
    complete_chat,
    format_memories,
    judge_prompt,
    parse_boxed_score,
    parse_correct_label,
    score_mcq,
)
from utils.config import PROJECT_ROOT, get_settings
from utils.run_meta import collect_run_meta

REPORTS = PROJECT_ROOT / "reports"
logger = logging.getLogger(__name__)
SCORE_CONCURRENCY = 5
JUDGE_PARSE_ATTEMPTS = 3
ADD_CONCURRENCY = 2
SAMPLE_SEARCH_CONCURRENCY = 2
QUESTION_SEARCH_CONCURRENCY = 2


def _run_dir(run_id: str) -> Path:
    path = REPORTS / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_run_meta(directory: Path, stage: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keep one meta block per stage in run_meta.json so add, search and score runs stay traceable."""
    meta = collect_run_meta(stage=stage, manifest=manifest)
    path = directory / "run_meta.json"
    stages: dict[str, Any] = {}
    if path.is_file():
        try:
            stages = json.loads(path.read_text(encoding="utf-8")).get("stages") or {}
        except ValueError:
            stages = {}
    stages[stage] = meta
    _write(path, {"run_id": directory.name, "stages": stages})
    return meta


def search_evidence(hit: dict[str, Any] | None) -> dict[str, Any]:
    """Per-question Search evidence for the score row: count, latency, and each item's id/score/content."""
    hit = hit or {}
    data = hit.get("data") or []
    return {
        "search": {
            "returned": len(data),
            "latency_ms": hit.get("latency_ms"),
            "evidence": [
                {"id": item.get("id"), "score": item.get("score"), "content": item.get("content")}
                for item in data
                if isinstance(item, dict)
            ],
        }
    }


def _partial_score_path(directory: Path) -> Path:
    return directory / "aml_score.partial.json"


def _parse_score_recovery_log(text: str, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    category_by_id = {row["question_id"]: row.get("category") for row in manifest["questions"]}
    recovered: dict[str, dict[str, Any]] = {}
    pattern = re.compile(r"INFO score \d+/\d+ (\S+) correct=(True|False)")
    for line in text.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        question_id = match.group(1)
        correct = match.group(2) == "True"
        recovered[question_id] = {
            "question_id": question_id,
            "category": category_by_id.get(question_id),
            "correct": correct,
            "score": 1.0 if correct else 0.0,
            "method": "correct_wrong",
        }
    return recovered


def _load_scored_rows(directory: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    partial_path = _partial_score_path(directory)
    if partial_path.is_file():
        payload = json.loads(partial_path.read_text(encoding="utf-8"))
        return {row["question_id"]: row for row in payload.get("results") or []}
    recovery_log = directory / "score_recovery.log"
    if recovery_log.is_file():
        return _parse_score_recovery_log(recovery_log.read_text(encoding="utf-8"), manifest)
    return {}


def _save_partial_score(
    directory: Path,
    *,
    run_id: str,
    manifest: dict[str, Any],
    answer_model: str,
    judge_model: str,
    rows: list[dict[str, Any]],
) -> None:
    correct = sum(1 for row in rows if row.get("correct"))
    _write(
        _partial_score_path(directory),
        {
            "run_id": run_id,
            "dataset_id": manifest["dataset_id"],
            "official": False,
            "score_sheet": _score_sheet(manifest["dataset_id"]),
            "answer_model": answer_model,
            "judge_model": judge_model,
            "total": len(rows),
            "correct": correct,
            "accuracy": (correct / len(rows)) if rows else 0.0,
            "complete": False,
            "results": rows,
        },
    )


def _question_row(sample: EvalSample, question: EvalQuestion) -> dict[str, Any]:
    row = {
        "dataset_id": sample.dataset_id,
        "sample_id": sample.sample_id,
        "question_id": question.question_id,
        "question": question.question,
        "gold": question.gold,
        "category": question.category,
        "options": question.options,
        "preference": question.preference,
    }
    if question.metadata:
        row["metadata"] = question.metadata
    return row


def _score_sheet(dataset_id: str) -> str:
    if dataset_id == "clbench":
        return "rubrics"
    if dataset_id.startswith("personamem"):
        return "preference"
    return "correct_wrong"


async def verdict_correct_wrong(fetch_judge: Callable[[], Awaitable[str]]) -> dict[str, Any]:
    """Parse a CORRECT/WRONG judge response. Retry twice, then record a failed question."""
    last_error: Exception | None = None
    for attempt in range(1, JUDGE_PARSE_ATTEMPTS + 1):
        judged = await fetch_judge()
        try:
            ok = parse_correct_label(judged)
        except ValueError as exc:
            last_error = exc
            logger.warning("judge parse failed attempt %s/%s: %s", attempt, JUDGE_PARSE_ATTEMPTS, exc)
            continue
        return {"correct": ok, "score": 1.0 if ok else 0.0, "method": "correct_wrong", "judge_raw": judged}
    return {
        "correct": False,
        "score": 0.0,
        "method": "judge_parse_error",
        "error": str(last_error) if last_error else "judge parse failed",
        "judge_raw": judged if JUDGE_PARSE_ATTEMPTS else None,
    }


async def run_overlapped_samples(
    samples: list[Any],
    add_sample: Callable[[Any], Awaitable[None]],
    after_add: Callable[[Any], Awaitable[None]],
) -> None:
    """Add the next sample while the current sample is searched and scored."""
    if not samples:
        return
    pending: asyncio.Task[None] | None = asyncio.create_task(add_sample(samples[0]))
    try:
        for index, sample in enumerate(samples):
            if pending is None:
                raise RuntimeError("sample add was not scheduled")
            await pending
            pending = None
            if index + 1 < len(samples):
                pending = asyncio.create_task(add_sample(samples[index + 1]))
            await after_add(sample)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()


async def run_parallel_conversations(
    samples: list[Any],
    add_sample: Callable[[Any], Awaitable[None]],
    after_add: Callable[[Any], Awaitable[None]],
    *,
    add_concurrency: int = ADD_CONCURRENCY,
    search_concurrency: int = SAMPLE_SEARCH_CONCURRENCY,
) -> None:
    """Add up to two conversations at once. Search a conversation only after its own Add finishes."""
    if not samples:
        return
    add_slots = asyncio.Semaphore(max(1, add_concurrency))
    search_slots = asyncio.Semaphore(max(1, search_concurrency))

    async def _one(sample: Any) -> None:
        async with add_slots:
            await add_sample(sample)
        async with search_slots:
            await after_add(sample)

    await asyncio.gather(*(_one(sample) for sample in samples))


async def search_first_then_parallel(
    questions: list[Any],
    search_one: Callable[[Any], Awaitable[Any]],
    score_one: Callable[[Any, Any], Awaitable[None]],
    *,
    rollup: asyncio.Semaphore,
    score_slots: asyncio.Semaphore,
    question_concurrency: int = QUESTION_SEARCH_CONCURRENCY,
) -> None:
    """Search the first question alone, then search the rest two at a time."""
    if not questions:
        return

    async def _score(question: Any, hit: Any) -> None:
        async with score_slots:
            await score_one(question, hit)

    async with rollup:
        first_hit = await search_one(questions[0])
    tasks = [asyncio.create_task(_score(questions[0], first_hit))]
    question_slots = asyncio.Semaphore(max(1, question_concurrency))

    async def _rest(question: Any) -> None:
        async with question_slots:
            hit = await search_one(question)
        await _score(question, hit)

    for question in questions[1:]:
        tasks.append(asyncio.create_task(_rest(question)))
    await asyncio.gather(*tasks)


async def search_then_score(
    questions: list[Any],
    search_one: Callable[[Any], Awaitable[Any]],
    score_one: Callable[[Any, Any], Awaitable[None]],
    *,
    concurrency: int = SCORE_CONCURRENCY,
) -> None:
    """Search questions one at a time. Score each hit as soon as it returns."""
    semaphore = asyncio.Semaphore(max(1, concurrency))
    tasks: list[asyncio.Task[None]] = []

    async def _score(question: Any, hit: Any) -> None:
        async with semaphore:
            await score_one(question, hit)

    for question in questions:
        hit = await search_one(question)
        tasks.append(asyncio.create_task(_score(question, hit)))
    if tasks:
        await asyncio.gather(*tasks)


def streaming_rounds(chunk_count: int) -> list[tuple[int, bool]]:
    """One search round after each written chunk. Only the last round is scored."""
    if chunk_count <= 0:
        return []
    return [(index + 1, index == chunk_count - 1) for index in range(chunk_count)]


def run_add(
    *,
    run_id: str,
    dataset_id: str,
    limit: int | None = None,
    paper: dict[str, Any] | None = None,
    paper_path: str | None = None,
    streaming: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    client = AmlClient(
        add_url=settings.aml_add_url,
        search_url=settings.aml_search_url,
        api_key=settings.aml_api_key,
        auth=settings.aml_auth,
        health_url=settings.aml_health_url,
    )
    client.health()
    from benchmark_data.aml_papers import ensure_runnable_dataset

    ensure_runnable_dataset(dataset_id, paper)
    samples = load_samples(dataset_id, limit=limit if paper is None else None, paper=paper)
    chunks_out: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    search_rounds: list[dict[str, Any]] = []
    for sample in samples:
        for question in sample.questions:
            questions.append(_question_row(sample, question))
        chunks = list(chunk_messages(sample.messages))
        for index, chunk in enumerate(chunks):
            body = add_body(
                run_id=run_id,
                dataset_id=sample.dataset_id,
                sample_id=sample.sample_id,
                chunk_index=index,
                messages=chunk,
            )
            logger.info("add %s chunk %s/%s", sample.sample_id, index + 1, len(chunks))
            response = client.add(body)
            chunks_out.append(
                {
                    "request_id": body["request_id"],
                    "user_id": body["user_id"],
                    "session_id": body["session_id"],
                    "messages": len(chunk),
                    "success": response.get("success") is True,
                }
            )
            if not streaming:
                continue
            round_no, final = streaming_rounds(len(chunks))[index]
            for question in sample.questions:
                search_body_payload = search_body(
                    run_id=run_id,
                    dataset_id=sample.dataset_id,
                    sample_id=sample.sample_id,
                    question=question,
                )
                found = client.search(search_body_payload)
                search_rounds.append(
                    {
                        "question_id": question.question_id,
                        "user_id": search_body_payload["user_id"],
                        "round": round_no,
                        "final": final,
                        "returned": len(found.get("data") or []),
                        "data": found.get("data") or [],
                    }
                )
    _write_run_meta(
        _run_dir(run_id),
        "add",
        {"dataset_id": dataset_id, "paper_id": (paper or {}).get("paper_id"), "paper_path": paper_path, "questions": questions},
    )
    report = {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "paper_id": (paper or {}).get("paper_id"),
        "paper_path": paper_path,
        "paper_tier": (paper or {}).get("tier"),
        "samples": len(samples),
        "questions": len(questions),
        "chunks": len(chunks_out),
        "word_count": "approximate: CJK character or Latin/number token; punctuation is a separator",
        "chunk_rule": "20 messages or 2000 approximate words, whichever comes first",
        "streaming": streaming,
        "streaming_searches": len(search_rounds),
        "adds": chunks_out,
    }
    directory = _run_dir(run_id)
    _write(directory / "aml_add.json", report)
    _write(
        directory / "aml_questions.json",
        {
            "run_id": run_id,
            "dataset_id": dataset_id,
            "paper_id": (paper or {}).get("paper_id"),
            "paper_path": paper_path,
            "questions": questions,
        },
    )
    if streaming:
        finals = [row for row in search_rounds if row["final"]]
        _write(
            directory / "aml_search.json",
            {
                "run_id": run_id,
                "dataset_id": dataset_id,
                "top_k": 100,
                "streaming": True,
                "results": finals,
            },
        )
        _write(
            directory / "aml_search_rounds.json",
            {"run_id": run_id, "dataset_id": dataset_id, "results": search_rounds},
        )
    return report


def _client() -> AmlClient:
    settings = get_settings()
    return AmlClient(
        add_url=settings.aml_add_url,
        search_url=settings.aml_search_url,
        api_key=settings.aml_api_key,
        auth=settings.aml_auth,
        health_url=settings.aml_health_url,
        timeout_sec=AML_HTTP_TIMEOUT_SEC,
    )


def _question_from_row(row: dict[str, Any]) -> EvalQuestion:
    return EvalQuestion(
        question_id=row["question_id"],
        question=row["question"],
        gold=row.get("gold") or "",
        category=row.get("category"),
        options=row.get("options"),
        preference=row.get("preference"),
        metadata=row.get("metadata") or {},
    )


def _search_hit(client: AmlClient, *, run_id: str, row: dict[str, Any]) -> dict[str, Any]:
    question = _question_from_row(row)
    body = search_body(
        run_id=run_id,
        dataset_id=row["dataset_id"],
        sample_id=row["sample_id"],
        question=question,
    )
    started = time.perf_counter()
    response = client.search(body)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "question_id": row["question_id"],
        "user_id": body["user_id"],
        "returned": len(response.get("data") or []),
        "latency_ms": latency_ms,
        "data": response.get("data") or [],
    }


async def _score_question_row(
    item: dict[str, Any],
    memories: str,
    *,
    answer_base: str,
    answer_key: str,
    answer_model: str,
    judge_base: str,
    judge_key: str,
    judge_model: str,
    hit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    question = _question_from_row(item)
    judged: str | None = None
    generated = await complete_chat(
        base_url=answer_base,
        api_key=answer_key,
        model=answer_model,
        prompt=answer_prompt(item["dataset_id"], question, memories),
    )
    if item["dataset_id"] == "clbench":
        from benchmark_data.aml_clbench import all_rubrics_passed, rubric_prompt

        rubrics = list((item.get("metadata") or {}).get("rubrics") or [])
        judged = await complete_chat(
            base_url=judge_base,
            api_key=judge_key,
            model=judge_model,
            prompt=rubric_prompt(rubrics, generated),
        )
        ok = all_rubrics_passed(judged, len(rubrics))
        verdict = {
            "correct": ok,
            "score": 1.0 if ok else 0.0,
            "method": "rubric_all",
            "rubrics": len(rubrics),
            "judge_raw": judged,
        }
    else:
        prompt = judge_prompt(item["dataset_id"], question, generated)
        if prompt is None:
            verdict = score_mcq(question, generated)
        elif item["dataset_id"].startswith("personamem_v2"):
            judged = await complete_chat(
                base_url=judge_base,
                api_key=judge_key,
                model=judge_model,
                prompt=prompt,
            )
            value = parse_boxed_score(judged)
            verdict = {"correct": value >= 0.5, "score": value, "method": "preference_judge", "judge_raw": judged}
        else:

            async def fetch_judge() -> str:
                return await complete_chat(
                    base_url=judge_base,
                    api_key=judge_key,
                    model=judge_model,
                    prompt=prompt,
                )

            verdict = await verdict_correct_wrong(fetch_judge)
    row = {
        "question_id": item["question_id"],
        "category": item.get("category"),
        **verdict,
        "gold": item.get("gold"),
        "generated": generated,
    }
    if hit is not None:
        row.update(search_evidence(hit))
    return row


def _ordered_score_rows(manifest: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [by_id[item["question_id"]] for item in manifest["questions"] if item["question_id"] in by_id]


def _finish_score_report(
    directory: Path,
    *,
    run_id: str,
    manifest: dict[str, Any],
    answer_model: str,
    judge_model: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    correct = sum(1 for row in rows if row.get("correct"))
    total = len(rows)
    report = {
        "run_id": run_id,
        "dataset_id": manifest["dataset_id"],
        "official": False,
        "score_sheet": _score_sheet(manifest["dataset_id"]),
        "note": (
            "CLBench is scored on its own sheet: a task passes only when every rubric passes."
            if manifest["dataset_id"] == "clbench"
            else "Local score uses the public answer-then-judge prompts. It is not the leaderboard score."
        ),
        "answer_model": answer_model,
        "judge_model": judge_model,
        "total": total,
        "correct": correct,
        "accuracy": (correct / total) if total else 0.0,
        "meta": _write_run_meta(directory, "score", manifest),
        "results": rows,
    }
    _write(directory / "aml_score.json", report)
    partial = _partial_score_path(directory)
    if partial.is_file():
        partial.unlink()
    return report


def run_search(*, run_id: str) -> dict[str, Any]:
    settings = get_settings()
    client = AmlClient(
        add_url=settings.aml_add_url,
        search_url=settings.aml_search_url,
        api_key=settings.aml_api_key,
        auth=settings.aml_auth,
    )
    manifest = json.loads((_run_dir(run_id) / "aml_questions.json").read_text(encoding="utf-8"))
    hits: list[dict[str, Any]] = []
    total = len(manifest["questions"])
    for index, row in enumerate(manifest["questions"], start=1):
        question = EvalQuestion(
            question_id=row["question_id"],
            question=row["question"],
            gold=row.get("gold") or "",
            category=row.get("category"),
            options=row.get("options"),
            preference=row.get("preference"),
        )
        body = search_body(
            run_id=run_id,
            dataset_id=row["dataset_id"],
            sample_id=row["sample_id"],
            question=question,
        )
        logger.info("search %s/%s %s", index, total, row["question_id"])
        started = time.perf_counter()
        response = client.search(body)
        hits.append(
            {
                "question_id": row["question_id"],
                "user_id": body["user_id"],
                "returned": len(response.get("data") or []),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "data": response.get("data") or [],
            }
        )
    report = {"run_id": run_id, "dataset_id": manifest["dataset_id"], "top_k": 100, "results": hits}
    _write(_run_dir(run_id) / "aml_search.json", report)
    _write_run_meta(_run_dir(run_id), "search", manifest)
    return report


async def run_search_and_score(
    *,
    run_id: str,
    output_dir: str | None = None,
    only_from: str | None = None,
) -> dict[str, Any]:
    """Search an existing Add, and score each question as soon as its search returns."""
    search_client = _client()
    search_client.health()
    source = _run_dir(run_id)
    directory = Path(output_dir) if output_dir else source
    directory.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((source / "aml_questions.json").read_text(encoding="utf-8"))
    if only_from:
        prior = json.loads(Path(only_from).read_text(encoding="utf-8"))
        keep = {str(row["question_id"]) for row in prior.get("results") or []}
        manifest = {
            **manifest,
            "questions": [row for row in manifest["questions"] if str(row["question_id"]) in keep],
        }
        _write(directory / "aml_questions.json", manifest)
        logger.info("score the %s questions already judged in %s", len(manifest["questions"]), only_from)
    elif directory != source and not (directory / "aml_questions.json").is_file():
        _write(directory / "aml_questions.json", manifest)
    _write_run_meta(directory, "search_and_score", manifest)
    questions = list(manifest["questions"])
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in questions:
        sample_id = str(row["sample_id"])
        if sample_id not in grouped:
            order.append(sample_id)
            grouped[sample_id] = []
        grouped[sample_id].append(row)

    hits: list[dict[str, Any]] = []
    search_path = directory / "aml_search.json"
    if search_path.is_file():
        saved_search = json.loads(search_path.read_text(encoding="utf-8"))
        hits = list(saved_search.get("results") or [])
    hit_by_id = {str(hit["question_id"]): hit for hit in hits}
    question_no = len(hits)
    total_questions = len(questions)
    endpoints = _score_endpoints()
    scored = _load_scored_rows(directory, manifest)
    if hits or scored:
        logger.info(
            "resume search %s/%s, score %s/%s",
            len(hits),
            total_questions,
            len(scored),
            total_questions,
        )
    state_lock = asyncio.Lock()
    rollup = asyncio.Semaphore(1)
    score_slots = asyncio.Semaphore(SCORE_CONCURRENCY)
    search_slots = asyncio.Semaphore(SAMPLE_SEARCH_CONCURRENCY)

    def _write_search() -> None:
        _write(
            directory / "aml_search.json",
            {"run_id": run_id, "dataset_id": manifest["dataset_id"], "top_k": 100, "results": hits},
        )

    async def _one(sample_id: str) -> None:
        nonlocal question_no
        sample_questions = grouped[sample_id]
        pending = [row for row in sample_questions if str(row["question_id"]) not in hit_by_id]
        already = [
            row
            for row in sample_questions
            if str(row["question_id"]) in hit_by_id and str(row["question_id"]) not in scored
        ]

        async def search_one(row: dict[str, Any]) -> dict[str, Any]:
            nonlocal question_no
            last_error: Exception | None = None
            hit: dict[str, Any] | None = None
            for attempt in range(3):
                try:
                    hit = await asyncio.to_thread(_search_hit, search_client, run_id=run_id, row=row)
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning("search retry %s/3 %s: %s", attempt + 1, row["question_id"], exc)
                    await asyncio.sleep(2)
            if hit is None:
                raise last_error or RuntimeError("search failed")
            async with state_lock:
                question_no += 1
                logger.info("search %s/%s %s", question_no, total_questions, row["question_id"])
                hits.append(hit)
                _write_search()
            return hit

        async def score_one(row: dict[str, Any], hit: dict[str, Any]) -> None:
            question = _question_from_row(row)
            memories = format_memories(hit.get("data") or [], question=question.question, options=question.options)
            last_error: Exception | None = None
            result: dict[str, Any] | None = None
            for attempt in range(3):
                try:
                    result = await _score_question_row(
                        row,
                        memories,
                        answer_base=endpoints["answer_base"],
                        answer_key=endpoints["answer_key"],
                        answer_model=endpoints["answer_model"],
                        judge_base=endpoints["judge_base"],
                        judge_key=endpoints["judge_key"],
                        judge_model=endpoints["judge_model"],
                        hit=hit,
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning("score retry %s/3 %s: %s", attempt + 1, row["question_id"], exc)
                    await asyncio.sleep(2)
            if result is None:
                raise last_error or RuntimeError("score failed")
            async with state_lock:
                scored[row["question_id"]] = result
                ordered = _ordered_score_rows(manifest, scored)
                logger.info(
                    "score %s/%s %s correct=%s",
                    len(ordered),
                    total_questions,
                    row["question_id"],
                    result.get("correct"),
                )
                _save_partial_score(
                    directory,
                    run_id=run_id,
                    manifest=manifest,
                    answer_model=endpoints["answer_model"],
                    judge_model=endpoints["judge_model"],
                    rows=ordered,
                )

        async def score_saved(row: dict[str, Any]) -> None:
            async with score_slots:
                await score_one(row, hit_by_id[str(row["question_id"])])

        if already:
            await asyncio.gather(*(score_saved(row) for row in already))
        if not pending:
            return
        async with search_slots:
            await search_first_then_parallel(
                pending,
                search_one,
                score_one,
                rollup=rollup,
                score_slots=score_slots,
            )

    await asyncio.gather(*(_one(sample_id) for sample_id in order))
    _write_search()
    score_report = _finish_score_report(
        directory,
        run_id=run_id,
        manifest=manifest,
        answer_model=endpoints["answer_model"],
        judge_model=endpoints["judge_model"],
        rows=_ordered_score_rows(manifest, scored),
    )
    return {"searched": len(hits), "score": score_report}


def _score_endpoints() -> dict[str, str]:
    settings = get_settings()
    return {
        "answer_key": settings.answer_api_key or settings.ark_api_key,
        "answer_base": settings.answer_api_base or settings.ark_api_base,
        "answer_model": settings.answer_model or settings.judge_model,
        "judge_key": settings.judge_api_key or settings.ark_api_key,
        "judge_base": settings.judge_api_base or settings.ark_api_base,
        "judge_model": settings.judge_model,
    }


async def _score_pending(
    *,
    run_id: str,
    directory: Path,
    manifest: dict[str, Any],
    pending: list[dict[str, Any]],
    memories_for: Callable[[dict[str, Any]], str],
    scored: dict[str, dict[str, Any]],
    endpoints: dict[str, str],
    hit_for: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Score pending questions with a bounded pool and checkpoint after each one."""
    total_questions = len(manifest["questions"])
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(SCORE_CONCURRENCY)

    async def _one(item: dict[str, Any]) -> None:
        async with semaphore:
            row = await _score_question_row(
                item,
                memories_for(item),
                answer_base=endpoints["answer_base"],
                answer_key=endpoints["answer_key"],
                answer_model=endpoints["answer_model"],
                judge_base=endpoints["judge_base"],
                judge_key=endpoints["judge_key"],
                judge_model=endpoints["judge_model"],
                hit=hit_for(item) if hit_for else None,
            )
        async with lock:
            scored[item["question_id"]] = row
            ordered = _ordered_score_rows(manifest, scored)
            logger.info(
                "score %s/%s %s correct=%s",
                len(ordered),
                total_questions,
                item["question_id"],
                row.get("correct"),
            )
            _save_partial_score(
                directory,
                run_id=run_id,
                manifest=manifest,
                answer_model=endpoints["answer_model"],
                judge_model=endpoints["judge_model"],
                rows=ordered,
            )

    if pending:
        await asyncio.gather(*(_one(item) for item in pending))
    return scored


async def run_score(*, run_id: str) -> dict[str, Any]:
    endpoints = _score_endpoints()
    directory = _run_dir(run_id)
    manifest = json.loads((directory / "aml_questions.json").read_text(encoding="utf-8"))
    search = json.loads((directory / "aml_search.json").read_text(encoding="utf-8"))
    search_by_id = {row["question_id"]: row for row in search["results"]}
    scored = _load_scored_rows(directory, manifest)
    if scored:
        logger.info("resume score from checkpoint: %s/%s done", len(scored), len(manifest["questions"]))
    pending = [item for item in manifest["questions"] if item["question_id"] not in scored]
    missing_search = [item["question_id"] for item in pending if item["question_id"] not in search_by_id]
    pending = [item for item in pending if item["question_id"] in search_by_id]

    def memories_for(item: dict[str, Any]) -> str:
        question = _question_from_row(item)
        return format_memories(
            (search_by_id.get(item["question_id"]) or {}).get("data") or [],
            question=question.question,
            options=question.options,
        )

    scored = await _score_pending(
        run_id=run_id,
        directory=directory,
        manifest=manifest,
        pending=pending,
        memories_for=memories_for,
        scored=scored,
        endpoints=endpoints,
        hit_for=lambda item: search_by_id.get(item["question_id"]),
    )
    if missing_search:
        raise RuntimeError(
            f"Search results are missing {len(missing_search)} questions; partial score was kept"
        )
    return _finish_score_report(
        directory,
        run_id=run_id,
        manifest=manifest,
        answer_model=endpoints["answer_model"],
        judge_model=endpoints["judge_model"],
        rows=_ordered_score_rows(manifest, scored),
    )


async def run_pipeline(
    *,
    run_id: str,
    dataset_id: str,
    limit: int | None = None,
    paper: dict[str, Any] | None = None,
    paper_path: str | None = None,
    skip_score: bool = False,
) -> dict[str, Any]:
    """Add each sample, then search and score it while the next sample is added."""
    from benchmark_data.aml_papers import ensure_runnable_dataset

    add_client = _client()
    search_client = _client()
    add_client.health()
    ensure_runnable_dataset(dataset_id, paper)
    samples = load_samples(dataset_id, limit=limit if paper is None else None, paper=paper)
    questions = [_question_row(sample, question) for sample in samples for question in sample.questions]
    directory = _run_dir(run_id)
    manifest = {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "paper_id": (paper or {}).get("paper_id"),
        "paper_path": paper_path,
        "questions": questions,
    }
    _write(directory / "aml_questions.json", manifest)
    _write_run_meta(directory, "pipeline", manifest)
    chunks_out: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    question_no = 0
    total_questions = len(questions)
    endpoints = _score_endpoints()
    scored: dict[str, dict[str, Any]] = {}
    state_lock = asyncio.Lock()
    rollup = asyncio.Semaphore(1)
    score_slots = asyncio.Semaphore(SCORE_CONCURRENCY)

    def _write_add() -> None:
        _write(
            directory / "aml_add.json",
            {
                "run_id": run_id,
                "dataset_id": dataset_id,
                "paper_id": (paper or {}).get("paper_id"),
                "paper_path": paper_path,
                "paper_tier": (paper or {}).get("tier"),
                "samples": len(samples),
                "questions": len(questions),
                "chunks": len(chunks_out),
                "word_count": "approximate: CJK character or Latin/number token; punctuation is a separator",
                "chunk_rule": "20 messages or 2000 approximate words, whichever comes first",
                "streaming": False,
                "pipeline": "parallel",
                "streaming_searches": 0,
                "adds": chunks_out,
            },
        )

    def _write_search() -> None:
        _write(
            directory / "aml_search.json",
            {"run_id": run_id, "dataset_id": dataset_id, "top_k": 100, "results": hits},
        )

    async def add_sample(sample: EvalSample) -> None:
        chunks = list(chunk_messages(sample.messages))
        for index, chunk in enumerate(chunks):
            body = add_body(
                run_id=run_id,
                dataset_id=sample.dataset_id,
                sample_id=sample.sample_id,
                chunk_index=index,
                messages=chunk,
            )
            logger.info("add %s chunk %s/%s", sample.sample_id, index + 1, len(chunks))
            response = await asyncio.to_thread(add_client.add, body)
            async with state_lock:
                chunks_out.append(
                    {
                        "request_id": body["request_id"],
                        "user_id": body["user_id"],
                        "session_id": body["session_id"],
                        "messages": len(chunk),
                        "success": response.get("success") is True,
                    }
                )
                _write_add()

    async def after_add(sample: EvalSample) -> None:
        nonlocal question_no
        sample_questions = [_question_row(sample, question) for question in sample.questions]

        async def search_one(row: dict[str, Any]) -> dict[str, Any]:
            nonlocal question_no
            hit = await asyncio.to_thread(_search_hit, search_client, run_id=run_id, row=row)
            async with state_lock:
                question_no += 1
                logger.info("search %s/%s %s", question_no, total_questions, row["question_id"])
                hits.append(hit)
                _write_search()
            return hit

        async def score_one(row: dict[str, Any], hit: dict[str, Any]) -> None:
            if skip_score:
                return
            question = _question_from_row(row)
            memories = format_memories(hit.get("data") or [], question=question.question, options=question.options)
            result = await _score_question_row(
                row,
                memories,
                answer_base=endpoints["answer_base"],
                answer_key=endpoints["answer_key"],
                answer_model=endpoints["answer_model"],
                judge_base=endpoints["judge_base"],
                judge_key=endpoints["judge_key"],
                judge_model=endpoints["judge_model"],
                hit=hit,
            )
            async with state_lock:
                scored[row["question_id"]] = result
                ordered = _ordered_score_rows(manifest, scored)
                logger.info(
                    "score %s/%s %s correct=%s",
                    len(ordered),
                    total_questions,
                    row["question_id"],
                    result.get("correct"),
                )
                _save_partial_score(
                    directory,
                    run_id=run_id,
                    manifest=manifest,
                    answer_model=endpoints["answer_model"],
                    judge_model=endpoints["judge_model"],
                    rows=ordered,
                )

        await search_first_then_parallel(
            sample_questions,
            search_one,
            score_one,
            rollup=rollup,
            score_slots=score_slots,
        )

    await run_parallel_conversations(samples, add_sample, after_add)
    _write_add()
    _write_search()
    score_report = None
    if not skip_score:
        score_report = _finish_score_report(
            directory,
            run_id=run_id,
            manifest=manifest,
            answer_model=endpoints["answer_model"],
            judge_model=endpoints["judge_model"],
            rows=_ordered_score_rows(manifest, scored),
        )
    return {"searched": len(hits), "score": score_report}
