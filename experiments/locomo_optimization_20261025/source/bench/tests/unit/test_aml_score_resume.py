"""AML score checkpoint resume."""

from benchmark_data.aml_models import EvalQuestion
from runners.aml_run import _parse_score_recovery_log


def test_parse_score_recovery_log():
    manifest = {
        "questions": [
            {"question_id": "conv-26#q0000", "category": "1"},
            {"question_id": "conv-26#q0001", "category": "2"},
        ]
    }
    text = "\n".join(
        [
            "INFO score 1/861 conv-26#q0000 correct=True",
            "INFO score 2/861 conv-26#q0001 correct=False",
        ]
    )
    rows = _parse_score_recovery_log(text, manifest)
    assert rows["conv-26#q0000"]["correct"] is True
    assert rows["conv-26#q0001"]["score"] == 0.0
    assert rows["conv-26#q0000"]["category"] == "1"
