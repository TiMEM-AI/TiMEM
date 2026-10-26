"""Configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

from utils.ids import scoped_session_id  # noqa: E402

__all__ = [
    "PROJECT_ROOT",
    "BenchmarkRunConfig",
    "IngestConfig",
    "Settings",
    "TimemSearchConfig",
    "get_settings",
    "load_reference_baselines",
    "load_yaml_config",
    "make_user_id",
    "scoped_session_id",
    "resolve_benchmark_config",
    "resolve_ingest_config",
    "resolve_retrieval_config",
    "RetrievalConfig",
]


@dataclass
class TimemSearchConfig:
    search_mode: str = "enhanced_semantic"
    use_hybrid: bool = True
    enable_memories_rethink: bool = False
    score_threshold: float = 0.0

    def to_overrides(self) -> dict[str, Any]:
        return {
            "search_mode": self.search_mode,
            "use_hybrid": self.use_hybrid,
            "enable_memories_rethink": self.enable_memories_rethink,
            "score_threshold": self.score_threshold,
        }


@dataclass
class IngestConfig:
    session_concurrency: int = 10
    mem0_session_concurrency: int = 10
    system_parallel: bool = True
    mem0_poll_mode: str = "deferred"
    mem0_poll_concurrency: int = 10

    def session_concurrency_for(self, system: str) -> int:
        """Per-system session limit (mem0 capped separately for rate limits)."""
        if system == "mem0":
            return min(self.mem0_session_concurrency, self.session_concurrency)
        return self.session_concurrency

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "session_concurrency": self.session_concurrency,
            "mem0_session_concurrency": self.mem0_session_concurrency,
            "system_parallel": self.system_parallel,
            "mem0_poll_mode": self.mem0_poll_mode,
            "mem0_poll_concurrency": self.mem0_poll_concurrency,
        }


@dataclass
class RetrievalConfig:
    query_concurrency: int = 10
    timem_query_concurrency: int = 3
    judge_concurrency: int = 10
    backfill_concurrency: int = 3
    system_parallel: bool = True
    pipeline_mode: bool = True
    judge_max_retries: int = 3
    judge_retry_base_sec: float = 2.0

    def query_concurrency_for(self, system: str) -> int:
        if system == "timem":
            return self.timem_query_concurrency
        return self.query_concurrency

    def to_report_dict(self, *, system: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "query_concurrency": self.query_concurrency,
            "timem_query_concurrency": self.timem_query_concurrency,
            "judge_concurrency": self.judge_concurrency,
            "backfill_concurrency": self.backfill_concurrency,
            "system_parallel": self.system_parallel,
            "pipeline_mode": self.pipeline_mode,
            "judge_max_retries": self.judge_max_retries,
            "judge_retry_base_sec": self.judge_retry_base_sec,
        }
        if system is not None:
            out["effective_query_concurrency"] = self.query_concurrency_for(system)
        return out


@dataclass
class BenchmarkRunConfig:
    preset: str = "stable"
    top_k: int = 10
    timem: TimemSearchConfig = field(default_factory=TimemSearchConfig)
    backfill_layers: list[str] = field(default_factory=lambda: ["L2", "L3", "L4", "L5"])

    @property
    def timem_overrides(self) -> dict[str, Any]:
        return self.timem.to_overrides()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    timem_base_url: str = Field(default="https://api.timem.cloud", alias="TIMEM_BASE_URL")
    timem_api_key: str = Field(default="", alias="TIMEM_API_KEY")
    timem_account_id: str = Field(default="", alias="TIMEM_ACCOUNT_ID")

    memos_api_key: str = Field(default="", alias="MEMOS_API_KEY")
    memos_base_url: str = Field(
        default="https://memos.memtensor.cn/api/openmem/v1",
        alias="MEMOS_BASE_URL",
    )

    mem0_api_key: str = Field(default="", alias="MEM0_API_KEY")
    mem0_base_url: str = Field(default="https://api.mem0.ai", alias="MEM0_BASE_URL")
    mem0_ingest_poll_timeout_sec: int = Field(default=120, alias="MEM0_INGEST_POLL_TIMEOUT_SEC")
    mem0_search_threshold: float = Field(default=0.0, alias="MEM0_SEARCH_THRESHOLD")

    ark_api_key: str = Field(default="", alias="ARK_API_KEY")
    ark_api_base: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3",
        alias="ARK_API_BASE",
    )
    judge_model: str = Field(default="", alias="JUDGE_MODEL")
    judge_temperature: float = Field(default=0.0, alias="JUDGE_TEMPERATURE")
    judge_api_key: str = Field(default="", alias="JUDGE_API_KEY")
    judge_api_base: str = Field(default="", alias="JUDGE_API_BASE")
    answer_api_key: str = Field(default="", alias="ANSWER_API_KEY")
    answer_api_base: str = Field(default="", alias="ANSWER_API_BASE")
    answer_model: str = Field(default="", alias="ANSWER_MODEL")

    aml_add_url: str = Field(default="", alias="AML_ADD_URL")
    aml_search_url: str = Field(default="", alias="AML_SEARCH_URL")
    aml_health_url: str = Field(default="", alias="AML_HEALTH_URL")
    aml_api_key: str = Field(default="", alias="AML_API_KEY")
    aml_auth: str = Field(default="x-api-key", alias="AML_AUTH")

    benchmark_expert_id: str = Field(default="benchmark", alias="BENCHMARK_EXPERT_ID")
    timem_memory_language: str = Field(default="en", alias="TIMEM_MEMORY_LANGUAGE")
    benchmark_top_k: int = Field(default=10, alias="BENCHMARK_TOP_K")
    benchmark_persona_count: int = Field(default=10, alias="BENCHMARK_PERSONA_COUNT")
    benchmark_backfill_timeout_sec: int = Field(default=600, alias="BENCHMARK_BACKFILL_TIMEOUT_SEC")


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_yaml_config() -> dict[str, Any]:
    path = PROJECT_ROOT / "config" / "default.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def make_user_id(system: str, run_id: str, persona_id: str) -> str:
    return f"{system}_{run_id}_{persona_id}"


def resolve_benchmark_config(
    *,
    preset: str | None = None,
    mode: Literal["T0", "T1"] = "T0",
    timem_overrides: dict[str, Any] | None = None,
    backfill_layers: list[str] | None = None,
) -> BenchmarkRunConfig:
    cfg = load_yaml_config()
    benchmark = cfg.get("benchmark", {})
    retrieval = cfg.get("retrieval", {})
    presets = cfg.get("presets", {})
    preset_name = preset or benchmark.get("default_preset", "stable")
    preset_cfg = presets.get(preset_name, presets.get("stable", {}))

    settings = get_settings()
    top_k = int(preset_cfg.get("top_k", settings.benchmark_top_k))

    search_mode = str(
        preset_cfg.get(
            "timem_search_mode",
            retrieval.get("timem_search_mode", "enhanced_semantic"),
        )
    )

    timem = TimemSearchConfig(
        search_mode=search_mode,
        use_hybrid=bool(preset_cfg.get("timem_use_hybrid", retrieval.get("timem_use_hybrid", True))),
        enable_memories_rethink=bool(
            preset_cfg.get(
                "timem_enable_memories_rethink",
                retrieval.get("timem_enable_memories_rethink", False),
            )
        ),
        score_threshold=float(retrieval.get("timem_score_threshold", 0.0)),
    )
    if timem_overrides:
        for key, value in timem_overrides.items():
            if hasattr(timem, key) and value is not None:
                setattr(timem, key, value)

    layers = backfill_layers or benchmark.get("backfill_layers", ["L2", "L3", "L4", "L5"])
    return BenchmarkRunConfig(
        preset=preset_name,
        top_k=top_k,
        timem=timem,
        backfill_layers=list(layers),
    )


def resolve_ingest_config(overrides: dict[str, Any] | None = None) -> IngestConfig:
    cfg = load_yaml_config().get("ingest", {})
    if overrides:
        cfg = {**cfg, **overrides}
    poll_mode = str(cfg.get("mem0_poll_mode", "deferred")).lower()
    if poll_mode not in ("sync", "deferred", "pipeline"):
        poll_mode = "deferred"
    return IngestConfig(
        session_concurrency=max(1, int(cfg.get("session_concurrency", 10))),
        mem0_session_concurrency=max(1, int(cfg.get("mem0_session_concurrency", 10))),
        system_parallel=bool(cfg.get("system_parallel", True)),
        mem0_poll_mode=poll_mode,
        mem0_poll_concurrency=max(1, int(cfg.get("mem0_poll_concurrency", 10))),
    )


def resolve_retrieval_config(overrides: dict[str, Any] | None = None) -> RetrievalConfig:
    cfg = load_yaml_config().get("retrieval", {})
    if overrides:
        cfg = {**cfg, **overrides}
    return RetrievalConfig(
        query_concurrency=max(1, int(cfg.get("query_concurrency", 10))),
        timem_query_concurrency=max(1, int(cfg.get("timem_query_concurrency", 3))),
        judge_concurrency=max(1, int(cfg.get("judge_concurrency", 10))),
        backfill_concurrency=max(1, int(cfg.get("backfill_concurrency", 3))),
        system_parallel=bool(cfg.get("system_parallel", True)),
        pipeline_mode=bool(cfg.get("pipeline_mode", True)),
        judge_max_retries=max(0, int(cfg.get("judge_max_retries", 3))),
        judge_retry_base_sec=max(0.1, float(cfg.get("judge_retry_base_sec", 2.0))),
    )


@lru_cache
def load_reference_baselines() -> dict[str, Any]:
    path = PROJECT_ROOT / "benchmark_data" / "reference_baselines.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
