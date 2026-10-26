"""AML text-track contract: chunking, request bodies, and local score parsing."""

from benchmark_data.aml_contract import (
    add_body,
    auth_headers,
    check_add_response,
    check_search_response,
    search_body,
)
from benchmark_data.aml_loaders import _locomo_messages
from benchmark_data.aml_models import EvalMessage, EvalQuestion
from benchmark_data.catalog import DATASETS, runnable_ids
from benchmark_data.chunking import chunk_messages, count_words
from evaluators.aml_score import extract_final_letter, parse_boxed_score, parse_correct_label, score_mcq


def test_word_count_splits_on_punctuation_and_counts_cjk():
    assert count_words("hello, world") == 2
    assert count_words("你好") == 2
    assert count_words("") == 0


def test_chunk_stops_at_20_messages_or_2000_words():
    short = [EvalMessage(role="user", content="one") for _ in range(21)]
    chunks = chunk_messages(short)
    assert [len(chunk) for chunk in chunks] == [20, 1]

    heavy = [
        EvalMessage(role="user", content="word " * 1500),
        EvalMessage(role="assistant", content="word " * 1500),
    ]
    split = chunk_messages(heavy)
    assert len(split) == 2
    assert len(split[0]) == 1


def test_add_and_search_bodies_follow_the_public_contract():
    messages = [EvalMessage(role="user", content="hello", timestamp_ms=1704067200000)]
    body = add_body(
        run_id="run1",
        dataset_id="locomo_refined",
        sample_id="conv-0",
        chunk_index=0,
        messages=messages,
    )
    assert set(body) == {"request_id", "messages", "user_id", "session_id"}
    assert body["request_id"] == "eval:run1:locomo_refined:conv-0:chunk-0"
    assert body["user_id"] == "eval:run1:locomo_refined:conv-0"
    assert body["messages"][0]["role"] == "user"
    assert check_add_response(body, {"success": True, **{k: body[k] for k in ("request_id", "user_id", "session_id")}}) is None

    open_q = EvalQuestion(question_id="q1", question="When?", gold="May")
    search = search_body(run_id="run1", dataset_id="locomo_refined", sample_id="conv-0", question=open_q)
    assert search["top_k"] == 100
    assert "options" not in search
    assert "filters" not in search

    choice = EvalQuestion(question_id="q2", question="Which?", gold="tea", options=["A. tea", "B. coffee"])
    with_options = search_body(run_id="run1", dataset_id="personamem_v2_32k", sample_id="p", question=choice)
    assert with_options["options"] == ["A. tea", "B. coffee"]
    assert check_search_response({"data": [{"id": "1", "content": "tea"}]}) is None
    assert check_search_response({"data": [{"id": str(i), "content": "x"} for i in range(101)]}) is not None


def test_default_auth_is_x_api_key():
    assert auth_headers("secret", "x-api-key") == {"X-Api-Key": "secret"}
    assert auth_headers("secret", "bearer")["Authorization"] == "Bearer secret"


def test_locomo_speakers_map_to_user_and_assistant_only():
    messages = _locomo_messages(
        {
            "session_1_date_time": "1:56 pm on 8 May, 2023",
            "session_1": [
                {"speaker": "Melanie", "text": "Hi"},
                {"speaker": "Caroline", "text": "Hello"},
            ],
        }
    )
    assert [item.role for item in messages] == ["assistant", "user"]
    assert all(item.timestamp_ms for item in messages)


def test_local_score_parsers():
    assert parse_correct_label('{"label": "CORRECT"}') is True
    assert parse_correct_label('{"label": "WRONG"}') is False
    assert parse_boxed_score(r"final \boxed{0.8}") == 0.8
    assert extract_final_letter("reason\nFinal Answer: B") == "B"
    verdict = score_mcq(
        EvalQuestion(question_id="q", question="q", gold="tea", options=["A. coffee", "B. tea"]),
        "Final Answer: B",
    )
    assert verdict["correct"] is True


def test_catalog_registers_seven_and_runs_six():
    assert len(DATASETS) == 7
    assert runnable_ids() == [
        "locomo_refined",
        "longmemeval_s",
        "personamem_v2_32k",
        "personamem_v2_128k",
        "beam",
        "clbench",
    ]
    assert DATASETS["scriptmem"].runnable is False
