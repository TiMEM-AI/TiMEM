"""Product API and AML Add/Search on one process."""

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from server.idempotency import IdempotencyStore
from server.model_settings import ModelSettingsStore
from server.settings import ServerSettings

# Optional local/monolith static UI. In Space-style compose the SPA is served by
# frontend (nginx); backend image does not ship frontend/dist, so this stays inactive.
DIST_DIR = Path(__file__).resolve().parents[1] / "frontend" / "dist"
AML_FORBIDDEN = {"metadata", "app_id", "agent_id", "async_mode", "filters", "rerank", "keyword_search"}


class AddBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    messages: List[dict]
    user_id: str
    session_id: str


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    user_id: str
    top_k: int = Field(gt=0)
    options: Optional[List[str]] = None


class ProductAddBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    messages: List[dict]
    session_id: Optional[str] = None


class ProductSearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    query: str
    top_k: int = 10


class GraphSearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    query: str
    limit: int = 20


class GraphQaBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    question: str
    hops: int = 1
    limit: int = 20


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    message: str
    session_id: Optional[str] = None


class ModelSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply_model: str = ""
    memory_model: str = ""
    base_url: str = ""
    api_key: str = ""
    target: str = ""
    model: str = ""
    dimensions: int = 0


class VerifyModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    target: str = ""
    dimensions: int = 0


def _extract_key(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    if header.lower().startswith("token "):
        return header[6:].strip()
    return request.headers.get("x-api-key", "").strip()


def _public_memory(item: dict, include_level: bool) -> dict:
    payload = {
        "id": item.get("id") or "",
        "title": item.get("title") or "",
        "content": item.get("content") or "",
        "score": item.get("score"),
        "created_at": item.get("created_at"),
    }
    if include_level and item.get("level"):
        payload["level"] = item["level"]
    return payload


def create_app(
    service,
    settings: ServerSettings,
    idempotency: Optional[IdempotencyStore] = None,
    model_settings: Optional[ModelSettingsStore] = None,
) -> FastAPI:
    app = FastAPI(title="TiMem")
    store = idempotency
    models = model_settings or ModelSettingsStore("data/model_settings.json")

    def require_key(request: Request) -> None:
        if not settings.api_key:
            raise HTTPException(status_code=500, detail="TIMEM_API_KEY is not set")
        if _extract_key(request) != settings.api_key:
            raise HTTPException(status_code=401, detail="invalid api key")

    def require_chat_key(request: Request) -> None:
        if settings.chat_requires_key:
            require_key(request)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/bootstrap")
    async def bootstrap():
        return {"chat_requires_key": settings.chat_requires_key, "deploy_mode": settings.deploy_mode}

    @app.get("/")
    async def index():
        page = DIST_DIR / "index.html"
        if page.is_file():
            return FileResponse(page)
        return JSONResponse({
            "message": "前端尚未构建。开发时在 frontend 目录运行 npm run dev；部署前运行 npm run build。",
        })

    @app.post("/v1/memories")
    async def product_add(body: ProductAddBody, request: Request):
        require_key(request)
        session_id = body.session_id or "default"
        chosen = models.credentials()
        await service.add(
            body.user_id,
            session_id,
            body.messages,
            memory_model=chosen["memory_model"],
            base_url=chosen["memory_base_url"],
            api_key=chosen["memory_api_key"],
            embed_model=chosen["embed_model"],
            embed_base_url=chosen["embed_base_url"],
            embed_api_key=chosen["embed_api_key"],
            embed_dim=chosen["embed_dim"],
        )
        return {"success": True, "user_id": body.user_id, "session_id": session_id}

    @app.post("/v1/memories/search")
    async def product_search(body: ProductSearchBody, request: Request):
        require_key(request)
        chosen = models.credentials()
        rows = await service.search(
            body.user_id,
            body.query,
            body.top_k,
            memory_model=chosen["memory_model"],
            base_url=chosen["memory_base_url"],
            api_key=chosen["memory_api_key"],
            embed_model=chosen["embed_model"],
            embed_base_url=chosen["embed_base_url"],
            embed_api_key=chosen["embed_api_key"],
            embed_dim=chosen["embed_dim"],
        )
        return {"data": [_public_memory(row, include_level=True) for row in rows]}

    @app.get("/v1/memories")
    async def product_list(
        user_id: str,
        request: Request,
        limit: int = 20,
        page: int = 1,
        level: str = "",
        q: str = "",
        start: str = "",
        end: str = "",
    ):
        require_chat_key(request)
        chosen = level.strip().upper()
        if chosen and chosen not in {"L1", "L2", "L3", "L4", "L5"}:
            raise HTTPException(status_code=422, detail="level must be L1, L2, L3, L4, or L5")
        result = await service.list_memories(
            user_id,
            limit=limit,
            page=page,
            level=chosen or None,
            keyword=q or None,
            start=start or None,
            end=end or None,
        )
        if isinstance(result, dict):
            rows = result.get("memories") or []
            meta = {
                "page": result.get("page", page),
                "page_size": result.get("page_size", limit),
                "has_more": bool(result.get("has_more")),
            }
        else:
            rows = result or []
            meta = {"page": 1, "page_size": limit, "has_more": False}
        return {"data": [_public_memory(row, include_level=True) for row in rows], **meta}

    @app.post("/v1/chat")
    async def chat(body: ChatBody, request: Request):
        if settings.chat_requires_key:
            require_key(request)
        session_id = body.session_id or "default"
        chosen = models.credentials()
        result = await service.chat(
            body.user_id,
            session_id,
            body.message,
            reply_model=chosen["reply_model"],
            memory_model=chosen["memory_model"],
            base_url=chosen["reply_base_url"],
            api_key=chosen["reply_api_key"],
            memory_base_url=chosen["memory_base_url"],
            memory_api_key=chosen["memory_api_key"],
            embed_model=chosen["embed_model"],
            embed_base_url=chosen["embed_base_url"],
            embed_api_key=chosen["embed_api_key"],
            embed_dim=chosen["embed_dim"],
        )
        return result

    @app.get("/v1/settings/models")
    async def get_model_settings(request: Request):
        require_chat_key(request)
        return models.get()

    @app.put("/v1/settings/models")
    async def put_model_settings(body: ModelSettingsBody, request: Request):
        require_chat_key(request)
        try:
            if body.target:
                return models.update_target(body.target, body.model, body.base_url, body.api_key, body.dimensions)
            return models.update(body.reply_model, body.memory_model, body.base_url, body.api_key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/settings/models/verify")
    async def verify_model(body: VerifyModelBody, request: Request):
        require_chat_key(request)
        creds = models.credentials()
        if body.target == "embed":
            base_url = (body.base_url or creds["embed_base_url"]).strip()
            api_key = body.api_key.strip() or creds["embed_api_key"]
            model_name = (body.model or creds["embed_model"]).strip()
        elif body.target in {"reply", "memory"}:
            base_url = (body.base_url or creds[f"{body.target}_base_url"]).strip()
            api_key = body.api_key.strip() or creds[f"{body.target}_api_key"]
            model_name = (body.model or creds[f"{body.target}_model"]).strip()
        else:
            base_url = (body.base_url or creds["reply_base_url"]).strip()
            api_key = body.api_key.strip() or creds["reply_api_key"]
            model_name = body.model.strip()
        if not base_url or not api_key or not model_name:
            return {"ok": False, "message": "接口地址、API Key 和模型名都要填写"}
        from llm.xingliu_adapter import XingliuAdapter

        adapter = XingliuAdapter()
        try:
            if body.target == "embed":
                dim = int(body.dimensions or creds["embed_dim"])
                response = await adapter.embed(
                    "hello",
                    model=model_name,
                    dimensions=dim,
                    base_url=base_url,
                    api_key=api_key,
                )
                return {"ok": True, "message": f"向量模型可用，维度 {len(response.embedding)}"}
            from llm.base_llm import Message, MessageRole
            response = await adapter.chat(
                [Message(role=MessageRole.USER, content="Reply with the single word ok.")],
                model=model_name,
                base_url=base_url,
                api_key=api_key,
                max_tokens=16,
            )
        except Exception as exc:
            return {"ok": False, "message": str(exc)[:300]}
        return {"ok": True, "message": f"模型可用：{(response.content or 'ok')[:80]}"}

    @app.post("/aml/add")
    async def aml_add(body: AddBody, request: Request):
        require_key(request)
        if store is not None:
            existing = store.get(body.request_id)
            if existing is not None:
                if existing.get("user_id") != body.user_id or existing.get("session_id") != body.session_id:
                    raise HTTPException(status_code=409, detail="request_id already used")
                return existing
        if not body.messages or not any((item.get("content") or "").strip() for item in body.messages):
            raise HTTPException(status_code=422, detail="messages are required")
        await service.add(
            body.user_id, body.session_id, body.messages, memory_model=models.aml_memory_model()
        )
        payload = {
            "success": True,
            "request_id": body.request_id,
            "user_id": body.user_id,
            "session_id": body.session_id,
        }
        if store is not None:
            store.put(body.request_id, payload)
        return payload

    @app.post("/aml/search")
    async def aml_search(body: SearchBody, request: Request):
        require_key(request)
        rows = await service.search(
            body.user_id, body.query, body.top_k, memory_model=models.aml_memory_model()
        )
        data = []
        for row in rows[: body.top_k]:
            item = _public_memory(row, include_level=False)
            item.pop("level", None)
            if not item["id"] or not item["content"]:
                continue
            data.append({
                "id": item["id"],
                "content": item["content"],
                "score": item["score"],
                "created_at": item["created_at"],
            })
        return {"data": data}

    @app.post("/v1/graph/search")
    async def graph_search(body: GraphSearchBody, request: Request):
        require_key(request)
        from timem.memory_graph.search import run_search

        if body.limit <= 0:
            raise HTTPException(status_code=400, detail="limit must be positive")
        view = await run_search(body.user_id, body.query, expert_id="assistant", limit=body.limit)
        view["answer"] = None
        return view

    @app.post("/v1/graph/qa")
    async def graph_qa(body: GraphQaBody, request: Request):
        require_key(request)
        from timem.memory_graph.qa import GraphQueryError, run_qa

        try:
            return await run_qa(
                body.user_id,
                body.question,
                expert_id="assistant",
                hops=body.hops,
                limit=body.limit,
            )
        except GraphQueryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Conditional: only when frontend/dist exists (local monorepo / monolith).
    # Split deploy: omit dist so /aml, /v1, /health are unaffected and nginx serves UI.
    if DIST_DIR.is_dir():
        app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="frontend")

    return app
