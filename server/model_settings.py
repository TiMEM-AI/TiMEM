"""Persisted OpenAI-compatible endpoint and model names."""

import json
import os
from pathlib import Path

from server.runtime_overrides import RuntimeOverridesStore


class ModelSettingsStore:
    def __init__(self, path: str, overrides: RuntimeOverridesStore | None = None):
        self.path = Path(path)
        self.overrides = overrides
        self.reply_default = os.getenv("XINGLIU_REPLY_MODEL", "qwen3.7-flash")
        self.memory_default = os.getenv("XINGLIU_CHAT_MODEL", "glm-5.3-flash")
        self.base_default = (
            os.getenv("XINGLIU_BASE_URL")
            or os.getenv("KSC_API_BASE")
            or "https://kspmas.ksyun.com/v1"
        ).rstrip("/")
        self.key_default = os.getenv("XINGLIU_API_KEY") or os.getenv("KSC_API_KEY") or ""
        self.embed_model = os.getenv("XINGLIU_EMBED_MODEL", "qwen3-embedding-8b")
        self.embed_dim = int(os.getenv("XINGLIU_EMBED_DIM", "2048"))

    def aml_memory_model(self) -> str:
        env_default = os.getenv("XINGLIU_CHAT_MODEL", "glm-5.3-flash")
        if self.overrides is not None:
            model, _source = self.overrides.resolve_memory_model(env_default)
            return model
        return env_default

    def aml_memory_model_resolved(self) -> dict:
        env_default = os.getenv("XINGLIU_CHAT_MODEL", "glm-5.3-flash")
        if self.overrides is not None:
            model, source = self.overrides.resolve_memory_model(env_default)
            return {"memory_model": model, "source": source, "env_default": env_default}
        return {"memory_model": env_default, "source": "env", "env_default": env_default}

    def _saved(self) -> dict:
        if not self.path.is_file():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _slot(self, saved: dict, key: str, default_model: str) -> dict:
        slot = saved.get(key) if isinstance(saved.get(key), dict) else {}
        flat_model = saved.get("reply_model" if key == "reply" else "memory_model")
        return {
            "base_url": (slot.get("base_url") or saved.get("base_url") or self.base_default).strip().rstrip("/"),
            "api_key": slot.get("api_key") or saved.get("api_key") or self.key_default,
            "model": (slot.get("model") or flat_model or default_model).strip(),
        }

    def _embed_slot(self, saved: dict) -> dict:
        slot = saved.get("embed") if isinstance(saved.get("embed"), dict) else {}
        return {
            "base_url": (slot.get("base_url") or saved.get("base_url") or self.base_default).strip().rstrip("/"),
            "api_key": slot.get("api_key") or saved.get("api_key") or self.key_default,
            "model": (slot.get("model") or self.embed_model).strip(),
            "dimensions": int(slot.get("dimensions") or self.embed_dim),
        }

    def credentials(self) -> dict:
        saved = self._saved()
        reply = self._slot(saved, "reply", self.reply_default)
        memory = self._slot(saved, "memory", self.memory_default)
        embed = self._embed_slot(saved)
        return {
            "reply_model": reply["model"],
            "reply_base_url": reply["base_url"],
            "reply_api_key": reply["api_key"],
            "memory_model": memory["model"],
            "memory_base_url": memory["base_url"],
            "memory_api_key": memory["api_key"],
            "embed_model": embed["model"],
            "embed_base_url": embed["base_url"],
            "embed_api_key": embed["api_key"],
            "embed_dim": embed["dimensions"],
        }

    def get(self) -> dict:
        creds = self.credentials()
        return {
            "reply_model": creds["reply_model"],
            "memory_model": creds["memory_model"],
            "base_url": creds["reply_base_url"],
            "api_key_set": bool(creds["reply_api_key"] or creds["memory_api_key"] or creds["embed_api_key"]),
            "embed_model": creds["embed_model"],
            "embed_dim": creds["embed_dim"],
            "reply": {
                "base_url": creds["reply_base_url"],
                "model": creds["reply_model"],
                "api_key_set": bool(creds["reply_api_key"]),
            },
            "memory": {
                "base_url": creds["memory_base_url"],
                "model": creds["memory_model"],
                "api_key_set": bool(creds["memory_api_key"]),
            },
            "embed": {
                "base_url": creds["embed_base_url"],
                "model": creds["embed_model"],
                "dimensions": creds["embed_dim"],
                "api_key_set": bool(creds["embed_api_key"]),
            },
        }

    def _write(self, reply: dict, memory: dict, embed: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"reply": reply, "memory": memory, "embed": embed}, ensure_ascii=False),
            encoding="utf-8",
        )
        return self.get()

    def _current(self) -> dict:
        creds = self.credentials()
        return {
            "reply": {
                "base_url": creds["reply_base_url"],
                "api_key": creds["reply_api_key"],
                "model": creds["reply_model"],
            },
            "memory": {
                "base_url": creds["memory_base_url"],
                "api_key": creds["memory_api_key"],
                "model": creds["memory_model"],
            },
            "embed": {
                "base_url": creds["embed_base_url"],
                "api_key": creds["embed_api_key"],
                "model": creds["embed_model"],
                "dimensions": creds["embed_dim"],
            },
        }

    def update_target(self, target: str, model: str, base_url: str = "", api_key: str = "", dimensions: int = 0) -> dict:
        if target not in {"reply", "memory", "embed"}:
            raise ValueError("请选择对话模型、记忆模型或向量模型")
        current = self._current()
        model = model.strip()
        base_url = (base_url or current[target]["base_url"]).strip().rstrip("/")
        if not model or not base_url:
            raise ValueError("接口地址和模型名都要填写")
        slot = {
            "base_url": base_url,
            "api_key": api_key.strip() or current[target]["api_key"],
            "model": model,
        }
        if target == "embed":
            slot["dimensions"] = int(dimensions or current["embed"]["dimensions"] or self.embed_dim)
        current[target] = slot
        return self._write(current["reply"], current["memory"], current["embed"])

    def update(self, reply_model: str, memory_model: str, base_url: str = "", api_key: str = "") -> dict:
        current = self._current()
        base_url = (base_url or current["reply"]["base_url"]).strip().rstrip("/")
        reply_model = reply_model.strip()
        memory_model = memory_model.strip()
        if not reply_model or not memory_model or not base_url:
            raise ValueError("接口地址和模型名都要填写")
        current["reply"] = {
            "base_url": base_url,
            "api_key": api_key.strip() or current["reply"]["api_key"],
            "model": reply_model,
        }
        current["memory"] = {
            "base_url": base_url,
            "api_key": api_key.strip() or current["memory"]["api_key"],
            "model": memory_model,
        }
        return self._write(current["reply"], current["memory"], current["embed"])
