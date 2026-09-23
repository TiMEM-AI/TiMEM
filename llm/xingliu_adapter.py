"""Kingsoft Xingliu (星流) OpenAI-compatible adapter.

Every chat request explicitly disables thinking. A rejection or a response
that still contains reasoning is a failure; the call is not retried with
thinking left on.
"""

import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from .base_llm import (
    BaseLLM,
    ChatResponse,
    EmbeddingResponse,
    Message,
    MessageRole,
    ModelConfig,
)
from llm.model_scope import current_runtime
from timem.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL = "https://kspmas.ksyun.com/v1"
DEFAULT_CHAT_MODEL = "glm-5.3-flash"
DEFAULT_EMBED_MODEL = "qwen3-embedding-8b"
DEFAULT_EMBED_DIM = 2048


class ThinkingNotDisabledError(RuntimeError):
    """The provider refused to turn thinking off, or still returned a trace."""


def build_chat_body(model: str, messages: List[Dict[str, str]], **params: Any) -> Dict[str, Any]:
    """Chat payload with thinking held to the minimum the model allows.

    GLM-5.3-flash rejects a full disable and then writes the trace into content.
    The shortest setting that returns only the answer is reasoning_effort=low.
    Other models get an explicit disable.
    """
    body = {
        "model": model,
        "messages": messages,
        "temperature": params.get("temperature", 0.7),
    }
    if "glm-5.3" in model.lower():
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = "low"
    else:
        body["enable_thinking"] = False
        body["thinking"] = {"type": "disabled"}
    if params.get("max_tokens"):
        body["max_tokens"] = params["max_tokens"]
    return body


def visible_answer(message: Dict[str, Any]) -> str:
    """Return the answer and drop a thinking trace if the model still emitted one."""
    content = message.get("content") or ""
    if "</think>" in content:
        content = content.split("</think>")[-1]
    content = content.strip()
    reasoning = (message.get("reasoning_content") or "").strip()
    if not content and reasoning:
        raise ThinkingNotDisabledError("response included reasoning_content")
    if "<think>" in content.lower() or "</think>" in content.lower():
        raise ThinkingNotDisabledError("response included a thinking trace")
    return content


class XingliuAdapter(BaseLLM):
    """Chat and embeddings against the Xingliu OpenAI-compatible API."""

    def __init__(self, config: Optional[ModelConfig] = None):
        model_name = os.getenv("XINGLIU_CHAT_MODEL", DEFAULT_CHAT_MODEL)
        super().__init__(config or ModelConfig(model_name=model_name))
        if config and config.model_name:
            self.config.model_name = config.model_name
        self.api_key = os.getenv("XINGLIU_API_KEY") or os.getenv("KSC_API_KEY") or ""
        self.base_url = (
            os.getenv("XINGLIU_BASE_URL")
            or os.getenv("KSC_API_BASE")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.embed_model = os.getenv("XINGLIU_EMBED_MODEL", DEFAULT_EMBED_MODEL)
        self.embed_dim = int(os.getenv("XINGLIU_EMBED_DIM", str(DEFAULT_EMBED_DIM)))
        self.timeout = int(os.getenv("XINGLIU_TIMEOUT", "120"))
        self.logger = logger

    def _headers(self, api_key: Optional[str] = None) -> Dict[str, str]:
        key = api_key or self.api_key
        if not key:
            raise RuntimeError("API Key 未填写")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }

    def _message_dict(self, message: Message) -> Dict[str, str]:
        role = message.role.value if isinstance(message.role, MessageRole) else str(message.role)
        return {"role": role, "content": message.content}

    async def _post(
        self,
        path: str,
        body: Dict[str, Any],
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        root = (base_url or self.base_url).rstrip("/")
        url = f"{root}{path}"
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=body, headers=self._headers(api_key)) as response:
                text = await response.text()
                if response.status >= 400:
                    lowered = text.lower()
                    if "thinking" in lowered or "enable_thinking" in lowered:
                        raise ThinkingNotDisabledError(text)
                    raise RuntimeError(f"HTTP {response.status}: {text[:300]}")
                import json
                return json.loads(text)

    async def chat(self, messages: List[Message], **kwargs) -> ChatResponse:
        runtime = current_runtime()
        model = kwargs.get("model") or (runtime.model if runtime else None) or self.config.model_name
        base_url = kwargs.get("base_url") or (runtime.base_url if runtime else None) or self.base_url
        api_key = kwargs.get("api_key") or (runtime.api_key if runtime else None) or self.api_key
        body = build_chat_body(
            model,
            [self._message_dict(message) for message in messages],
            temperature=kwargs.get("temperature", self.config.temperature),
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
        )
        start = time.time()
        payload = await self._post("/chat/completions", body, base_url=base_url, api_key=api_key)
        choice = payload["choices"][0]
        message = choice.get("message") or {}
        usage = payload.get("usage") or {}
        return ChatResponse(
            content=visible_answer(message),
            finish_reason=choice.get("finish_reason") or "stop",
            model=payload.get("model") or model,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            response_time=time.time() - start,
        )

    async def chat_stream(self, messages: List[Message], **kwargs) -> AsyncIterator[str]:
        response = await self.chat(messages, **kwargs)
        yield response.content

    async def complete(self, prompt: str, **kwargs) -> str:
        response = await self.chat([Message(role=MessageRole.USER, content=prompt)], **kwargs)
        return response.content

    async def embed(self, text: str, **kwargs) -> EmbeddingResponse:
        runtime = current_runtime()
        model = kwargs.get("model") or (runtime.embed_model if runtime and runtime.embed_model else None) or self.embed_model
        dim = int(kwargs.get("dimensions") or (runtime.embed_dim if runtime and runtime.embed_dim else 0) or self.embed_dim)
        base_url = kwargs.get("base_url") or (runtime.embed_base_url if runtime and runtime.embed_base_url else None) or self.base_url
        api_key = kwargs.get("api_key") or (runtime.embed_api_key if runtime and runtime.embed_api_key else None) or self.api_key
        body = {"model": model, "input": text, "dimensions": dim, "encoding_format": "float"}
        start = time.time()
        payload = await self._post("/embeddings", body, base_url=base_url, api_key=api_key)
        data = payload.get("data") or []
        vector = (data[0] or {}).get("embedding") if data else None
        if not isinstance(vector, list) or len(vector) != dim:
            length = len(vector) if isinstance(vector, list) else 0
            raise RuntimeError(f"embedding length {length} != {dim}")
        usage = payload.get("usage") or {}
        return EmbeddingResponse(
            embedding=vector,
            model=payload.get("model") or model,
            usage={"prompt_tokens": usage.get("prompt_tokens", 0), "total_tokens": usage.get("total_tokens", 0)},
            response_time=time.time() - start,
        )

    async def embed_batch(self, texts: List[str], **kwargs) -> List[EmbeddingResponse]:
        return [await self.embed(text, **kwargs) for text in texts]

    async def summarize(self, text: str, **kwargs) -> str:
        return await self.complete(text, **kwargs)

    async def validate_model(self, model_name: str) -> bool:
        return bool(model_name)

    async def get_model_info(self, model_name: str) -> Dict[str, Any]:
        return {"id": model_name, "provider": "xingliu"}
