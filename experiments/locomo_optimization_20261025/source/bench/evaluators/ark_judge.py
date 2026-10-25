"""Volcengine ARK (OpenAI-compatible) LLM judge for retrieval quality."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from models.records import MemoryRecord
from utils.config import get_settings

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """You are an impartial evaluator for memory retrieval systems.

Given a user question, a gold reference answer, and retrieved memory snippets,
decide whether the memories contain enough information to answer the question correctly.

Return ONLY valid JSON:
{{
  "can_answer": true or false,
  "score": 0.0 to 1.0,
  "matched_facts": ["..."],
  "reason": "one short sentence"
}}

Question:
{question}

Gold answer:
{gold}

Retrieved memories:
{memories}
"""


def _parse_judge_json(text: str) -> dict[str, Any]:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"can_answer": False, "score": 0.0, "matched_facts": [], "reason": text}
    try:
        data = json.loads(match.group(0))
        return {
            "can_answer": bool(data.get("can_answer")),
            "score": float(data.get("score", 0.0)),
            "matched_facts": data.get("matched_facts") or [],
            "reason": data.get("reason", ""),
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"can_answer": False, "score": 0.0, "matched_facts": [], "reason": text}


def _retryable_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return int(status) if status is not None else None


def _is_retryable(exc: Exception) -> bool:
    status = _retryable_status(exc)
    return status in (429, 500, 502, 503, 504)


class ARKJudge:
    """Judge via Volcengine ARK chat completions API."""

    def __init__(
        self,
        *,
        max_retries: int = 3,
        retry_base_sec: float = 2.0,
    ) -> None:
        self._settings = get_settings()
        self._client = None
        self._async_client = None
        self._max_retries = max(0, max_retries)
        self._retry_base_sec = max(0.1, retry_base_sec)

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self._settings.ark_api_key,
                base_url=self._settings.ark_api_base.rstrip("/"),
            )
        return self._client

    def _get_async_client(self) -> Any:
        if self._async_client is None:
            from openai import AsyncOpenAI

            self._async_client = AsyncOpenAI(
                api_key=self._settings.ark_api_key,
                base_url=self._settings.ark_api_base.rstrip("/"),
            )
        return self._async_client

    async def close_async(self) -> None:
        if self._async_client is not None:
            await self._async_client.close()
            self._async_client = None

    def _build_prompt(self, question: str, gold: str, records: list[MemoryRecord]) -> str:
        memories_text = "\n".join(
            f"- [{r.memory_type}] {r.content[:500]}" for r in records[:10]
        ) or "(empty)"
        return JUDGE_PROMPT.format(question=question, gold=gold, memories=memories_text)

    def _attach_usage(self, parsed: dict[str, Any], resp: Any, judge_latency_ms: float) -> dict[str, Any]:
        usage = getattr(resp, "usage", None)
        if usage is not None:
            parsed["judge_tokens"] = int(getattr(usage, "total_tokens", 0) or 0)
            parsed["judge_prompt_tokens"] = int(getattr(usage, "prompt_tokens", 0) or 0)
            parsed["judge_completion_tokens"] = int(
                getattr(usage, "completion_tokens", 0) or 0
            )
        else:
            parsed["judge_tokens"] = 0
        parsed["judge_latency_ms"] = judge_latency_ms
        return parsed

    def judge(self, question: str, gold: str, records: list[MemoryRecord]) -> dict[str, Any]:
        if not self._settings.ark_api_key:
            return {
                "can_answer": False,
                "score": 0.0,
                "matched_facts": [],
                "reason": "ARK_API_KEY not configured",
                "skipped": True,
            }

        prompt = self._build_prompt(question, gold, records)

        try:
            client = self._get_client()
            started = time.perf_counter()
            resp = client.chat.completions.create(
                model=self._settings.judge_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self._settings.judge_temperature,
            )
            judge_latency_ms = (time.perf_counter() - started) * 1000
            content = resp.choices[0].message.content or ""
            parsed = _parse_judge_json(content)
            return self._attach_usage(parsed, resp, judge_latency_ms)
        except Exception as exc:
            logger.error("ARK judge failed: %s", exc)
            return {
                "can_answer": False,
                "score": 0.0,
                "matched_facts": [],
                "reason": str(exc),
                "error": True,
            }

    async def _create_completion_async(self, prompt: str) -> tuple[Any, float]:
        client = self._get_async_client()
        delay = self._retry_base_sec
        last_exc: Exception | None = None
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                started = time.perf_counter()
                resp = await client.chat.completions.create(
                    model=self._settings.judge_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self._settings.judge_temperature,
                )
                return resp, (time.perf_counter() - started) * 1000
            except Exception as exc:
                last_exc = exc
                if _is_retryable(exc) and attempt < attempts - 1:
                    logger.warning(
                        "ARK async judge retry %s/%s (status=%s): %s",
                        attempt + 1,
                        attempts,
                        _retryable_status(exc),
                        exc,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("ARK async judge failed")

    async def judge_async(
        self,
        question: str,
        gold: str,
        records: list[MemoryRecord],
    ) -> dict[str, Any]:
        """Async judge for concurrent retrieval evaluation."""
        if not self._settings.ark_api_key:
            return {
                "can_answer": False,
                "score": 0.0,
                "matched_facts": [],
                "reason": "ARK_API_KEY not configured",
                "skipped": True,
            }

        prompt = self._build_prompt(question, gold, records)

        try:
            resp, judge_latency_ms = await self._create_completion_async(prompt)
            content = resp.choices[0].message.content or ""
            parsed = _parse_judge_json(content)
            return self._attach_usage(parsed, resp, judge_latency_ms)
        except Exception as exc:
            logger.error("ARK async judge failed: %s", exc)
            return {
                "can_answer": False,
                "score": 0.0,
                "matched_facts": [],
                "reason": str(exc),
                "error": True,
            }


# Backward-compatible alias
LLMJudge = ARKJudge
