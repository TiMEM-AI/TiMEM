"""aml-eval paper JSON filters."""

from benchmark_data.aml_loaders import load_samples
from benchmark_data.aml_papers import apply_paper, load_paper, resolve_paper_path
from utils.config import PROJECT_ROOT


def test_resolve_paper_under_repo():
    path = resolve_paper_path("aml-eval/datasets/locomo_refined/papers/daily.json")
    assert path.is_file()
    paper = load_paper(path)
    assert paper["dataset_id"] == "locomo_refined"
    assert paper["tier"] == "daily"


def test_apply_paper_locomo_daily_subset():
    samples = load_samples("locomo_refined")
    paper = load_paper(PROJECT_ROOT / "aml-eval/datasets/locomo_refined/papers/daily.json")
    filtered = apply_paper(samples, paper)
    assert len(filtered) == 1
    assert filtered[0].sample_id == "conv-26"
    assert len(filtered[0].questions) == len(paper["question_ids"])
