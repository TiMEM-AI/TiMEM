"""BEAM, CLBench, and streaming wiring. These tests use tiny fixtures, not the full papers."""

import pytest

from benchmark_data.aml_beam import beam_row_to_sample
from benchmark_data.aml_clbench import all_rubrics_passed, clbench_row_to_sample
from benchmark_data.aml_loaders import _load_beam
from benchmark_data.aml_models import EvalMessage, EvalQuestion, EvalSample
from benchmark_data.aml_papers import apply_paper
from runners.aml_run import streaming_rounds


def test_beam_row_keeps_turns_and_probing_answers():
    row = {
        "conversation_id": 7,
        "chat": [
            [
                {"role": "user", "content": "We launched on Monday.", "time_anchor": "March-15-2024"},
                {"role": "assistant", "content": "Noted."},
            ]
        ],
        "probing_questions": (
            "{'abstention': [{'question': 'What changed in the UI?', "
            "'ideal_response': 'The chat does not say.', 'difficulty': 'medium'}]}"
        ),
    }
    sample = beam_row_to_sample(row, split="100K")
    assert sample.dataset_id == "beam"
    assert sample.sample_id == "7"
    assert [item.role for item in sample.messages] == ["user", "assistant"]
    assert sample.messages[0].timestamp_ms is not None
    assert sample.questions[0].question_id == "7#abstention#0"
    assert sample.questions[0].gold == "The chat does not say."


def test_beam_rejects_unpublished_10m():
    with pytest.raises(ValueError, match="100K"):
        _load_beam({"split": "10M"})


def test_clbench_stores_context_and_keeps_the_last_user_turn_as_the_question():
    sample = clbench_row_to_sample(
        {
            "messages": [
                {"role": "system", "content": "The policy is net-30."},
                {"role": "user", "content": "What is the payment term?"},
            ],
            "rubrics": ["Mentions net-30.", "Does not invent a discount."],
            "metadata": {"task_id": "task-1", "context_id": "ctx-1", "category": "policy"},
        }
    )
    assert sample.sample_id == "task-1"
    assert [item.content for item in sample.messages] == ["The policy is net-30."]
    question = sample.questions[0]
    assert question.question_id == "task-1"
    assert question.question == "What is the payment term?"
    assert question.metadata["rubrics"] == ["Mentions net-30.", "Does not invent a discount."]


def test_clbench_passes_only_when_every_rubric_is_yes():
    assert all_rubrics_passed("yes\nyes\n", 2) is True
    assert all_rubrics_passed("yes\nno\n", 2) is False
    assert all_rubrics_passed("yes\n", 2) is False


def test_daily_task_ids_drop_other_questions_on_the_same_sample():
    sample = EvalSample(
        dataset_id="clbench",
        sample_id="shared",
        messages=[EvalMessage(role="user", content="context")],
        questions=[
            EvalQuestion(question_id="keep", question="a", gold="a"),
            EvalQuestion(question_id="drop", question="b", gold="b"),
        ],
    )
    filtered = apply_paper([sample], {"task_ids": ["keep"]})
    assert [item.question_id for item in filtered[0].questions] == ["keep"]


def test_streaming_scores_only_the_last_round():
    rounds = streaming_rounds(3)
    assert rounds == [(1, False), (2, False), (3, True)]
    assert streaming_rounds(0) == []
