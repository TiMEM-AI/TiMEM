"""Build and check the public AML Add/Search bodies. No vendor search flags."""

from __future__ import annotations

from typing import Any

from benchmark_data.aml_models import EvalMessage, EvalQuestion

TOP_K = 100
# AML platform: one Add/Search request may run up to 30 minutes (api-guide).
AML_HTTP_TIMEOUT_SEC = 1800.0


def user_id_for(run_id: str, dataset_id: str, sample_id: str) -> str:
    return f"eval:{run_id}:{dataset_id}:{sample_id}"


def session_id_for(run_id: str, sample_id: str) -> str:
    return f"eval:{run_id}:sample:{sample_id}"


def request_id_for(run_id: str, dataset_id: str, sample_id: str, chunk_index: int) -> str:
    return f"eval:{run_id}:{dataset_id}:{sample_id}:chunk-{chunk_index}"


def add_body(
    *,
    run_id: str,
    dataset_id: str,
    sample_id: str,
    chunk_index: int,
    messages: list[EvalMessage],
) -> dict[str, Any]:
    payload_messages: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.timestamp_ms is not None:
            item["timestamp"] = int(message.timestamp_ms)
        payload_messages.append(item)
    return {
        "request_id": request_id_for(run_id, dataset_id, sample_id, chunk_index),
        "messages": payload_messages,
        "user_id": user_id_for(run_id, dataset_id, sample_id),
        "session_id": session_id_for(run_id, sample_id),
    }


def search_body(*, run_id: str, dataset_id: str, sample_id: str, question: EvalQuestion) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": question.question,
        "user_id": user_id_for(run_id, dataset_id, sample_id),
        "top_k": TOP_K,
    }
    if question.options:
        body["options"] = list(question.options)
    return body


def auth_headers(api_key: str, auth: str) -> dict[str, str]:
    mode = (auth or "x-api-key").strip().lower().replace("_", "-")
    if not api_key:
        return {}
    if mode in {"bearer", "token"}:
        scheme = "Bearer" if mode == "bearer" else "Token"
        return {"Authorization": f"{scheme} {api_key}"}
    return {"X-Api-Key": api_key}


def check_add_response(body: dict[str, Any], response: Any) -> str | None:
    if not isinstance(response, dict):
        return "Add response must be a JSON object"
    if response.get("success") is not True:
        return "Add response success must be true"
    for key in ("request_id", "user_id", "session_id"):
        if response.get(key) != body.get(key):
            return f"Add response {key} must echo the request"
    return None


def check_search_response(response: Any, *, top_k: int = TOP_K) -> str | None:
    if not isinstance(response, dict):
        return "Search response must be a JSON object"
    data = response.get("data")
    if not isinstance(data, list):
        return "Search response data must be an array"
    if len(data) > top_k:
        return f"Search returned {len(data)} items, above top_k {top_k}"
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            return f"Search data[{index}] must be an object"
        if not str(item.get("id") or "").strip():
            return f"Search data[{index}].id is required"
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            return f"Search data[{index}].content must be a non-empty string"
    return None
