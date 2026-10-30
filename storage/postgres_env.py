"""POSTGRES_* env overrides for docker-compose / production."""

from __future__ import annotations

import os
from typing import MutableMapping


def apply_postgres_env(config: MutableMapping) -> MutableMapping:
    """POSTGRES_HOST/PORT/USER/PASSWORD/DB(ATABASE) override YAML (compose and .env set them)."""
    if os.getenv("POSTGRES_HOST"):
        config["host"] = os.getenv("POSTGRES_HOST")
    if os.getenv("POSTGRES_PORT"):
        config["port"] = int(os.getenv("POSTGRES_PORT"))
    if os.getenv("POSTGRES_USER"):
        config["user"] = os.getenv("POSTGRES_USER")
    if os.getenv("POSTGRES_PASSWORD"):
        config["password"] = os.getenv("POSTGRES_PASSWORD")
    if os.getenv("POSTGRES_DB") or os.getenv("POSTGRES_DATABASE"):
        config["database"] = os.getenv("POSTGRES_DB") or os.getenv("POSTGRES_DATABASE")
    return config
