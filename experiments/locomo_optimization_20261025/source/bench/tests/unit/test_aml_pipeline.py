"""Per-sample AML pipeline scheduling and judge-parse retries."""

from __future__ import annotations

import asyncio

from runners.aml_run import (
    JUDGE_PARSE_ATTEMPTS,
    run_overlapped_samples,
    run_parallel_conversations,
    search_first_then_parallel,
    search_then_score,
    verdict_correct_wrong,
)


async def test_judge_parse_retries_then_records_failure():
    calls = {"n": 0}

    async def fetch() -> str:
        calls["n"] += 1
        return "not json"

    verdict = await verdict_correct_wrong(fetch)
    assert calls["n"] == JUDGE_PARSE_ATTEMPTS
    assert verdict["correct"] is False
    assert verdict["method"] == "judge_parse_error"


async def test_judge_parse_accepts_the_second_response():
    texts = iter(["nope", '{"label": "CORRECT"}'])

    async def fetch() -> str:
        return next(texts)

    verdict = await verdict_correct_wrong(fetch)
    assert verdict == {"correct": True, "score": 1.0, "method": "correct_wrong", "judge_raw": '{"label": "CORRECT"}'}


async def test_next_sample_add_overlaps_current_work():
    events: list[str] = []

    async def add_sample(sample: str) -> None:
        events.append(f"add-start {sample}")
        await asyncio.sleep(0.02)
        events.append(f"add-end {sample}")

    async def after_add(sample: str) -> None:
        events.append(f"work-start {sample}")
        await asyncio.sleep(0.05)
        events.append(f"work-end {sample}")

    await run_overlapped_samples(["a", "b"], add_sample, after_add)
    assert events.index("add-end a") < events.index("work-start a")
    assert events.index("add-start b") < events.index("work-end a")
    assert events.index("add-end b") < events.index("work-start b")


async def test_search_stays_serial_while_scores_overlap():
    events: list[str] = []
    searching = {"n": 0}

    async def search_one(question: str) -> str:
        searching["n"] += 1
        assert searching["n"] == 1
        events.append(f"search-start {question}")
        await asyncio.sleep(0.01)
        events.append(f"search-end {question}")
        searching["n"] -= 1
        return question

    async def score_one(question: str, hit: str) -> None:
        events.append(f"score-start {question}")
        await asyncio.sleep(0.04)
        events.append(f"score-end {question}")

    await search_then_score(["q1", "q2"], search_one, score_one, concurrency=5)
    assert events.index("search-end q1") < events.index("search-start q2")
    assert events.index("score-start q1") < events.index("search-end q2")
    assert events.index("score-end q1") > events.index("search-start q2")


async def test_two_conversations_add_at_once():
    active = {"n": 0}
    peak = {"n": 0}

    async def add_sample(sample: str) -> None:
        active["n"] += 1
        peak["n"] = max(peak["n"], active["n"])
        await asyncio.sleep(0.03)
        active["n"] -= 1

    async def after_add(sample: str) -> None:
        await asyncio.sleep(0.01)

    await run_parallel_conversations(["a", "b", "c"], add_sample, after_add, add_concurrency=2, search_concurrency=2)
    assert peak["n"] == 2


async def test_first_search_is_alone_then_the_rest_overlap():
    events: list[str] = []
    searching = {"n": 0}

    async def search_one(question: str) -> str:
        searching["n"] += 1
        if question == "q1":
            assert searching["n"] == 1
        events.append(f"search-start {question}")
        await asyncio.sleep(0.02 if question == "q1" else 0.03)
        events.append(f"search-end {question}")
        searching["n"] -= 1
        return question

    async def score_one(question: str, hit: str) -> None:
        await asyncio.sleep(0)

    rollup = asyncio.Semaphore(1)
    score_slots = asyncio.Semaphore(5)
    await search_first_then_parallel(
        ["q1", "q2", "q3"],
        search_one,
        score_one,
        rollup=rollup,
        score_slots=score_slots,
        question_concurrency=2,
    )
    assert events.index("search-end q1") < events.index("search-start q2")
    assert events.index("search-end q1") < events.index("search-start q3")
    assert events.index("search-start q2") < events.index("search-end q3")
