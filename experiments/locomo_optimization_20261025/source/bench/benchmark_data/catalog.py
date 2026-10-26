"""AML text-track datasets. Runnable sets are loaded; the rest are registered only."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DatasetEntry:
    dataset_id: str
    title: str
    runnable: bool
    source: str
    note: str


DATASETS: dict[str, DatasetEntry] = {
    "locomo_refined": DatasetEntry(
        dataset_id="locomo_refined",
        title="LoCoMo-Refined",
        runnable=True,
        source="https://github.com/mem-eval-suite/LoCoMo_refined",
        note="Public refined conversations and QA. Image questions are skipped.",
    ),
    "longmemeval_s": DatasetEntry(
        dataset_id="longmemeval_s",
        title="LongMemEval-S",
        runnable=True,
        source="https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned",
        note="longmemeval_s_cleaned.json, 500 questions, evidence turns marked has_answer.",
    ),
    "personamem_v2_32k": DatasetEntry(
        dataset_id="personamem_v2_32k",
        title="PersonaMem-v2 32k",
        runnable=True,
        source="https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2",
        note="Text track, 32k chat histories. Scored separately from 128k.",
    ),
    "personamem_v2_128k": DatasetEntry(
        dataset_id="personamem_v2_128k",
        title="PersonaMem-v2 128k",
        runnable=True,
        source="https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2",
        note="Text track, 128k chat histories. Scored separately from 32k.",
    ),
    "beam": DatasetEntry(
        dataset_id="beam",
        title="BEAM",
        runnable=True,
        source="https://huggingface.co/datasets/Mohammadta/BEAM",
        note="Public 100K, 500K, and 1M splits. The unpublished 10M split is not loaded. Scored correct or wrong.",
    ),
    "clbench": DatasetEntry(
        dataset_id="clbench",
        title="CLBench",
        runnable=True,
        source="https://huggingface.co/datasets/tencent/CL-bench",
        note="A task scores only when every rubric passes. That score stays on its own sheet.",
    ),
    "scriptmem": DatasetEntry(
        dataset_id="scriptmem",
        title="ScriptMem",
        runnable=False,
        source="https://github.com/memorax-ai/ScriptMem",
        note="Registered only. Public release has 457 questions and no conversation text.",
    ),
}


def get_dataset(dataset_id: str) -> DatasetEntry:
    try:
        return DATASETS[dataset_id]
    except KeyError as exc:
        known = ", ".join(DATASETS)
        raise KeyError(f"Unknown dataset {dataset_id!r}. Known: {known}") from exc


def runnable_ids() -> list[str]:
    return [item.dataset_id for item in DATASETS.values() if item.runnable]
