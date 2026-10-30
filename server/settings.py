"""Runtime settings for the self-hosted service."""

import os
from dataclasses import dataclass


@dataclass
class ServerSettings:
    api_key: str
    deploy_mode: str
    host: str
    port: int
    expert_id: str = "assistant"
    reply_model: str = "qwen3.7-flash"
    admin_secret: str = ""
    aml_lock_models: bool = False
    runtime_overrides_path: str = "data/runtime_overrides.json"

    @property
    def chat_requires_key(self) -> bool:
        return self.deploy_mode == "server"

    @property
    def admin_configured(self) -> bool:
        return bool(self.admin_secret)

    @classmethod
    def from_env(cls) -> "ServerSettings":
        mode = os.getenv("TIMEM_DEPLOY_MODE", "local").strip().lower()
        if mode not in {"local", "server"}:
            mode = "local"
        host = os.getenv("TIMEM_HOST")
        if not host:
            host = "0.0.0.0" if mode == "server" else "127.0.0.1"
        admin = (
            os.getenv("TIMEM_ADMIN_PASSWORD")
            or os.getenv("TIMEM_ADMIN_KEY")
            or ""
        ).strip()
        lock = os.getenv("TIMEM_AML_LOCK_MODELS", "").strip().lower() in {"1", "true", "yes", "on"}
        return cls(
            api_key=os.getenv("TIMEM_API_KEY", ""),
            deploy_mode=mode,
            host=host,
            port=int(os.getenv("TIMEM_PORT", "8000")),
            reply_model=os.getenv("XINGLIU_REPLY_MODEL", "qwen3.7-flash"),
            admin_secret=admin,
            aml_lock_models=lock,
            runtime_overrides_path=os.getenv(
                "TIMEM_RUNTIME_OVERRIDES_PATH", "data/runtime_overrides.json"
            ),
        )