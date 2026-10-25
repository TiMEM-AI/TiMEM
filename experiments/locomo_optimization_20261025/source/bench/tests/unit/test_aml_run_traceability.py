"""Score rows keep the answer, judge output, and Search evidence; runs record code and model metadata."""

from __future__ import annotations

import json
from pathlib import Path

import runners.aml_run as aml_run
from utils.run_meta import collect_run_meta, git_state


ROW = {
    "dataset_id": "locomo_refined",
    "sample_id": "conv-1",
    "question_id": "conv-1#q0001",
    "question": "Where does Blair work?",
    "gold": "Redbird Clinic",
    "category": "4",
    "options": None,
    "preference": None,
}
HIT = {
    "question_id": "conv-1#q0001",
    "user_id": "eval:r:locomo_refined:conv-1",
    "returned": 2,
    "latency_ms": 812.5,
    "data": [
        {"id": "m1", "score": 0.9, "content": "Blair works at Redbird Clinic."},
        {"id": "m2", "score": 0.4, "content": "Blair lives in Austin."},
    ],
}


def test_search_evidence_keeps_ids_scores_contents_and_latency():
    out = aml_run.search_evidence(HIT)["search"]
    assert out["returned"] == 2
    assert out["latency_ms"] == 812.5
    assert out["evidence"][0] == {"id": "m1", "score": 0.9, "content": "Blair works at Redbird Clinic."}


def test_search_evidence_handles_missing_hit():
    assert aml_run.search_evidence(None)["search"] == {"returned": 0, "latency_ms": None, "evidence": []}


async def test_score_row_records_generated_judge_raw_and_evidence(monkeypatch):
    replies = iter(["Redbird Clinic", '{"label": "CORRECT", "reason": "matches"}'])

    async def fake_chat(**kwargs):
        return next(replies)

    monkeypatch.setattr(aml_run, "complete_chat", fake_chat)
    row = await aml_run._score_question_row(
        ROW,
        "memories",
        answer_base="b",
        answer_key="k",
        answer_model="m",
        judge_base="b",
        judge_key="k",
        judge_model="m",
        hit=HIT,
    )
    assert row["correct"] is True
    assert row["generated"] == "Redbird Clinic"
    assert row["gold"] == "Redbird Clinic"
    assert "matches" in row["judge_raw"]
    assert row["search"]["returned"] == 2
    assert row["search"]["evidence"][1]["id"] == "m2"


async def test_score_row_without_hit_has_no_search_block(monkeypatch):
    replies = iter(["unknown", '{"label": "WRONG"}'])

    async def fake_chat(**kwargs):
        return next(replies)

    monkeypatch.setattr(aml_run, "complete_chat", fake_chat)
    row = await aml_run._score_question_row(
        ROW, "", answer_base="b", answer_key="k", answer_model="m", judge_base="b", judge_key="k", judge_model="m"
    )
    assert row["correct"] is False
    assert row["generated"] == "unknown"
    assert "search" not in row


def test_search_hit_records_latency():
    class FakeClient:
        def search(self, body):
            return {"data": [{"id": "x", "content": "c"}]}

    hit = aml_run._search_hit(FakeClient(), run_id="r", row=ROW)
    assert hit["returned"] == 1
    assert isinstance(hit["latency_ms"], float) and hit["latency_ms"] >= 0


def test_git_state_on_non_repo_reports_error(tmp_path: Path):
    state = git_state(tmp_path)
    assert state["dir"] == str(tmp_path)
    assert "error" in state


def test_git_state_on_benchmark_repo_has_commit():
    state = git_state(Path(aml_run.PROJECT_ROOT))
    assert len(state.get("commit", "")) == 40
    assert isinstance(state.get("dirty"), bool)


def test_collect_run_meta_reads_backend_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AML_BACKEND_DIR", str(tmp_path))
    monkeypatch.setenv("AML_BACKEND_COMMIT", "abc1234")
    monkeypatch.setenv("AML_BACKEND_NOTE", "worktree + stash")
    meta = collect_run_meta(stage="score", manifest={"dataset_id": "small", "paper_id": "p", "questions": [1, 2]})
    assert meta["backend"]["commit_declared"] == "abc1234"
    assert meta["backend"]["note"] == "worktree + stash"
    assert meta["dataset_id"] == "small" and meta["questions"] == 2
    assert meta["models"]["judge_temperature"] == 0.0
    assert "commit" in meta["benchmark"]


def test_run_meta_file_keeps_each_stage(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(aml_run, "collect_run_meta", lambda *, stage, manifest=None: {"stage": stage})
    aml_run._write_run_meta(tmp_path, "add")
    aml_run._write_run_meta(tmp_path, "score")
    saved = json.loads((tmp_path / "run_meta.json").read_text(encoding="utf-8"))
    assert set(saved["stages"]) == {"add", "score"}