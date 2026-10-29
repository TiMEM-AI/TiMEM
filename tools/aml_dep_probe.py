#!/usr/bin/env python3
"""Probe AML Add/Search for missing modules under Dockerfile-like deps (no torch)."""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
import uuid
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _print_report(missing: List[str], errors: List[str]) -> None:
    print("==== MISSING MODULES ====")
    for m in missing:
        print("-", m)
    print("==== ERRORS ====")
    for e in errors:
        print("-", e)


def _collect_module_missing(exc: BaseException, missing: List[str], errors: List[str], where: str) -> bool:
    found = False
    cur: BaseException | None = exc
    seen = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ModuleNotFoundError):
            missing.append(getattr(cur, "name", None) or str(cur))
            errors.append(f"{where}: {cur}")
            found = True
        if isinstance(cur, ImportError) and "No module named" in str(cur):
            missing.append(str(cur))
            errors.append(f"{where}: {cur}")
            found = True
        cur = cur.__cause__ or cur.__context__
    return found


async def _run() -> int:
    missing: List[str] = []
    errors: List[str] = []

    try:
        import server.app  # noqa: F401
        print("OK import server.app")
    except Exception as e:
        _collect_module_missing(e, missing, errors, "import server.app")
        errors.append(f"import server.app: {type(e).__name__}: {e}")
        traceback.print_exc()
        _print_report(missing, errors)
        return 2

    try:
        import llm.embedding_service  # noqa: F401
        import llm.qwen_embedding_adapter  # noqa: F401
        import llm.qwen_local_adapter  # noqa: F401
        if "torch" in sys.modules:
            errors.append("torch unexpectedly loaded by adapter import")
        else:
            print("OK embedding/qwen adapters import without torch")
    except Exception as e:
        _collect_module_missing(e, missing, errors, "embedding import")
        traceback.print_exc()
        _print_report(missing, errors)
        return 2

    # Build app like python -m server
    try:
        from server.app import create_app
        from server.engine import EngineMemoryService
        from server.idempotency import IdempotencyStore
        from server.model_settings import ModelSettingsStore
        from server.settings import ServerSettings
        import httpx
        from httpx import ASGITransport
    except Exception as e:
        _collect_module_missing(e, missing, errors, "create_app imports")
        traceback.print_exc()
        _print_report(missing, errors)
        return 2

    settings = ServerSettings.from_env()
    if not settings.api_key:
        # local .env should set it; fall back for probe
        os.environ.setdefault("TIMEM_API_KEY", "dep-probe-key")
        settings = ServerSettings.from_env()

    app = create_app(
        EngineMemoryService(reply_model=settings.reply_model),
        settings,
        IdempotencyStore("data/aml_dep_probe_adds.sqlite"),
        ModelSettingsStore("data/aml_dep_probe_model_settings.json"),
    )

    api_key = settings.api_key
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    uid = f"dep-probe-{uuid.uuid4().hex[:8]}"
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    rid = str(uuid.uuid4())

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=180.0) as client:
        r = await client.get("/health")
        print(f"OK /health -> {r.status_code} {r.text}")

        add_body = {
            "request_id": rid,
            "user_id": uid,
            "session_id": sid,
            "messages": [
                {"role": "user", "content": "I live in Shanghai and like coffee.", "timestamp": 1730000000000},
                {"role": "assistant", "content": "Noted.", "timestamp": 1730000001000},
            ],
        }
        search_body = {"user_id": uid, "query": "Where do I live?", "top_k": 5}

        for path, body in (("/aml/add", add_body), ("/aml/search", search_body)):
            try:
                r = await client.post(path, json=body, headers=headers)
                print(f"RESP {path} -> {r.status_code} {r.text[:800]}")
                text = r.text or ""
                if "No module named" in text or "ModuleNotFoundError" in text:
                    missing.append(text)
                    errors.append(f"{path} leaked ModuleNotFound: {text[:400]}")
                    _print_report(missing, errors)
                    return 2
                if r.status_code >= 500:
                    errors.append(f"{path} HTTP {r.status_code}: {text[:400]}")
                elif path == "/aml/add" and r.status_code == 200:
                    print("OK /aml/add")
                elif path == "/aml/search" and r.status_code == 200:
                    print("OK /aml/search")
            except Exception as e:
                if _collect_module_missing(e, missing, errors, path):
                    traceback.print_exc()
                    _print_report(missing, errors)
                    return 2
                errors.append(f"{path}: {type(e).__name__}: {e}")
                traceback.print_exc()

    _print_report(missing, errors)
    if missing:
        return 2
    add_ok = any("OK /aml/add" in e or True for e in [])  # noqa - use errors check below
    # Determine success from printed responses via errors list
    if any(e.startswith("/aml/add HTTP") or e.startswith("/aml/search HTTP") for e in errors):
        print("PROBE_PARTIAL: no ModuleNotFoundError; Add/Search still failing for other reasons")
        return 1
    if errors:
        print("PROBE_PARTIAL: no ModuleNotFoundError; other errors remain")
        return 1
    print("PROBE_OK: Add/Search succeeded without ModuleNotFoundError")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
