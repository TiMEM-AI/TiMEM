"""python -m server"""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from server.app import create_app
from server.engine import EngineMemoryService
from server.idempotency import IdempotencyStore
from server.model_settings import ModelSettingsStore
from server.runtime_overrides import RuntimeOverridesStore
from server.settings import ServerSettings


def main() -> None:
    settings = ServerSettings.from_env()
    if settings.deploy_mode == "server" and not settings.api_key:
        raise SystemExit("TIMEM_API_KEY is required when TIMEM_DEPLOY_MODE=server")
    overrides = RuntimeOverridesStore(settings.runtime_overrides_path)
    app = create_app(
        EngineMemoryService(reply_model=settings.reply_model),
        settings,
        IdempotencyStore(os.getenv("TIMEM_IDEMPOTENCY_PATH", "data/aml_adds.sqlite")),
        ModelSettingsStore(
            os.getenv("TIMEM_MODEL_SETTINGS_PATH", "data/model_settings.json"),
            overrides=overrides,
        ),
    )
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()