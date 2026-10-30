"""AML and product contract tests. The memory engine is faked."""

from fastapi.testclient import TestClient

from server.app import create_app
from server.idempotency import IdempotencyStore
from server.model_settings import ModelSettingsStore
from server.settings import ServerSettings
from llm.xingliu_adapter import ThinkingNotDisabledError, build_chat_body, visible_answer
from timem.utils.session_recall import limit_l1_to_top_sessions


class FakeMemory:
    def __init__(self):
        self.adds = []
        self.searches = []

    async def add(self, user_id, session_id, messages, memory_model=None, **kwargs):
        self.adds.append((user_id, session_id, messages, memory_model))

    async def search(self, user_id, query, top_k, memory_model=None, **kwargs):
        self.searches.append((user_id, query, top_k, memory_model))
        rows = [
            {"id": f"{user_id}-{i}", "content": f"memory {i} {query}", "score": 1 - i * 0.1, "created_at": "2026-01-01T00:00:00Z", "level": "L1"}
            for i in range(5)
        ]
        return rows[:top_k]

    async def list_memories(self, user_id, limit=50, **kwargs):
        return [{"id": "m1", "content": "listed", "level": "L2", "title": "会话", "score": None, "created_at": None}]

    async def chat(self, user_id, session_id, message, reply_model=None, memory_model=None, **kwargs):
        return {"reply": f"echo {message}", "memories": []}


def _client(tmp_path, mode="local", admin_secret=""):
    settings = ServerSettings(
        api_key="secret",
        deploy_mode=mode,
        host="127.0.0.1",
        port=8000,
        admin_secret=admin_secret,
        runtime_overrides_path=str(tmp_path / "runtime_overrides.json"),
    )
    store = IdempotencyStore(str(tmp_path / "adds.sqlite"))
    from server.runtime_overrides import RuntimeOverridesStore
    models = ModelSettingsStore(
        str(tmp_path / "models.json"),
        overrides=RuntimeOverridesStore(str(tmp_path / "runtime_overrides.json")),
    )
    memory = FakeMemory()
    return TestClient(create_app(memory, settings, store, models)), memory, models


def test_health_is_open(tmp_path):
    client, _, _ = _client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200


def test_aml_add_search_contract(tmp_path):
    client, _, _ = _client(tmp_path)
    headers = {"Authorization": "Bearer secret"}
    add = client.post("/aml/add", headers=headers, json={
        "request_id": "req-1",
        "user_id": "user-a",
        "session_id": "sess-1",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert add.status_code == 200
    assert add.json() == {
        "success": True,
        "request_id": "req-1",
        "user_id": "user-a",
        "session_id": "sess-1",
    }
    again = client.post("/aml/add", headers=headers, json={
        "request_id": "req-1",
        "user_id": "user-a",
        "session_id": "sess-1",
        "messages": [{"role": "user", "content": "hello again"}],
    })
    assert again.status_code == 200
    assert again.json()["request_id"] == "req-1"

    search = client.post("/aml/search", headers=headers, json={
        "query": "hello",
        "user_id": "user-a",
        "top_k": 2,
    })
    body = search.json()
    assert search.status_code == 200
    assert list(body.keys()) == ["data"]
    assert len(body["data"]) == 2
    assert "answer" not in body
    for item in body["data"]:
        assert set(item.keys()) == {"id", "content", "score", "created_at"}


def test_aml_rejects_metadata_and_missing_key(tmp_path):
    client, _, _ = _client(tmp_path)
    denied = client.post("/aml/search", json={"query": "q", "user_id": "u", "top_k": 1})
    assert denied.status_code == 401
    rejected = client.post(
        "/aml/add",
        headers={"X-Api-Key": "secret"},
        json={
            "request_id": "req-2",
            "user_id": "u",
            "session_id": "s",
            "messages": [{"role": "user", "content": "x"}],
            "metadata": {"a": 1},
        },
    )
    assert rejected.status_code == 422


def test_local_chat_does_not_require_key_but_product_api_does(tmp_path):
    client, _, _ = _client(tmp_path, mode="local")
    chat = client.post("/v1/chat", json={"user_id": "u", "message": "hi"})
    assert chat.status_code == 200
    assert chat.json()["reply"] == "echo hi"
    memories = client.post("/v1/memories/search", json={"user_id": "u", "query": "hi"})
    assert memories.status_code == 401
    listed = client.get("/v1/memories", params={"user_id": "u"})
    assert listed.status_code == 200
    assert listed.json()["data"][0]["content"] == "listed"
    assert listed.json()["data"][0]["level"] == "L2"
    assert listed.json()["page"] == 1
    unknown = client.get("/v1/memories", params={"user_id": "u", "level": "L9"})
    assert unknown.status_code == 422


def test_server_memory_list_requires_key(tmp_path):
    client, _, _ = _client(tmp_path, mode="server")
    denied = client.get("/v1/memories", params={"user_id": "u"})
    assert denied.status_code == 401
    allowed = client.get("/v1/memories", params={"user_id": "u"}, headers={"X-Api-Key": "secret"})
    assert allowed.status_code == 200


def test_server_chat_requires_key(tmp_path):
    client, _, _ = _client(tmp_path, mode="server")
    open_chat = client.post("/v1/chat", json={"user_id": "u", "message": "hi"})
    assert open_chat.status_code == 401
    closed = client.post(
        "/v1/chat",
        headers={"X-Api-Key": "secret"},
        json={"user_id": "u", "message": "hi"},
    )
    assert closed.status_code == 200


def test_search_scopes_are_separate_user_ids(tmp_path):
    client, _, _ = _client(tmp_path)
    headers = {"Authorization": "Token secret"}
    one = client.post("/aml/search", headers=headers, json={"query": "q", "user_id": "alpha", "top_k": 1})
    two = client.post("/aml/search", headers=headers, json={"query": "q", "user_id": "beta", "top_k": 1})
    assert one.json()["data"][0]["id"].startswith("alpha-")
    assert two.json()["data"][0]["id"].startswith("beta-")


def test_top_sessions_keeps_more_than_the_best_one():
    memories = [
        {"id": "1", "session_id": "s1", "content": "a"},
        {"id": "2", "session_id": "s2", "content": "b"},
        {"id": "3", "session_id": "s3", "content": "c"},
        {"id": "4", "session_id": "s4", "content": "d"},
    ]
    scores = [("s1", 0.9), ("s2", 0.5), ("s4", 0.2), ("s3", 0.1)]
    kept = limit_l1_to_top_sessions(memories, scores, top_n=3)
    assert {item["session_id"] for item in kept} == {"s1", "s2", "s4"}


def test_chat_body_disables_thinking():
    glm = build_chat_body("glm-5.3-flash", [{"role": "user", "content": "hi"}])
    assert glm["reasoning_effort"] == "low"
    assert glm["thinking"] == {"type": "enabled"}
    qwen = build_chat_body("qwen3.7-flash", [{"role": "user", "content": "hi"}])
    assert qwen["enable_thinking"] is False
    assert qwen["thinking"] == {"type": "disabled"}
    assert visible_answer({"content": "trace</think>ok", "reasoning_content": ""}) == "ok"
    try:
        visible_answer({"content": "", "reasoning_content": "trace"})
    except ThinkingNotDisabledError:
        return
    raise AssertionError("reasoning_content should fail the call")


def test_model_settings_saved_for_product_not_aml(tmp_path, monkeypatch):
    monkeypatch.setenv("XINGLIU_CHAT_MODEL", "glm-5.3-flash")
    client, memory, _models = _client(tmp_path)
    saved = client.put("/v1/settings/models", json={
        "reply_model": " qwen3.7-flash ",
        "memory_model": " custom-mem ",
    })
    assert saved.status_code == 200
    body = saved.json()
    assert body["reply_model"] == "qwen3.7-flash"
    assert body["memory_model"] == "custom-mem"
    assert body["embed_dim"] == 2048
    assert "api_key" not in body
    assert body["base_url"].startswith("http")

    client.post("/v1/memories", headers={"X-Api-Key": "secret"}, json={
        "user_id": "u",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert memory.adds[-1][3] == "custom-mem"

    client.post("/aml/add", headers={"X-Api-Key": "secret"}, json={
        "request_id": "req-models",
        "user_id": "u",
        "session_id": "s",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert memory.adds[-1][3] == "glm-5.3-flash"


def test_model_settings_do_not_return_the_provider_key(tmp_path):
    client, _, _ = _client(tmp_path)
    saved = client.put("/v1/settings/models", json={
        "reply_model": "qwen3.7-flash",
        "memory_model": "glm-5.3-flash",
        "base_url": "https://example.com/v1",
        "api_key": "sk-secret-value",
    })
    assert saved.status_code == 200
    assert saved.json()["api_key_set"] is True
    assert "sk-secret-value" not in saved.text
    checked = client.post("/v1/settings/models/verify", json={"model": ""})
    assert checked.json()["ok"] is False


def test_server_mode_model_settings_require_key(tmp_path):
    client, _, _ = _client(tmp_path, mode="server")
    assert client.get("/v1/settings/models").status_code == 401
    ok = client.get("/v1/settings/models", headers={"X-Api-Key": "secret"})
    assert ok.status_code == 200


def test_admin_required_for_runtime_models(tmp_path):
    client, memory, models = _client(tmp_path, admin_secret="admin-secret")
    denied = client.put("/v1/admin/runtime-models", json={"memory_model": "deepseek-v4.1-flash"})
    assert denied.status_code == 401
    with_api = client.put(
        "/v1/admin/runtime-models",
        headers={"X-Api-Key": "secret"},
        json={"memory_model": "deepseek-v4.1-flash"},
    )
    assert with_api.status_code == 401
    ok = client.put(
        "/v1/admin/runtime-models",
        headers={"X-Admin-Key": "admin-secret"},
        json={"memory_model": "deepseek-v4.1-flash"},
    )
    assert ok.status_code == 200
    assert ok.json()["memory_model"] == "deepseek-v4.1-flash"
    assert ok.json()["source"] == "override"
    client.post(
        "/aml/add",
        headers={"X-Api-Key": "secret"},
        json={
            "request_id": "req-hot",
            "user_id": "u",
            "session_id": "s",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert memory.adds[-1][3] == "deepseek-v4.1-flash"


def test_registration_key_cannot_write_runtime_models(tmp_path):
    client, _, _ = _client(tmp_path, mode="server", admin_secret="admin-secret")
    denied = client.put(
        "/v1/settings/models",
        headers={"X-Api-Key": "secret"},
        json={"reply_model": "qwen3.7-flash", "memory_model": "custom-mem"},
    )
    assert denied.status_code == 401
    allowed = client.put(
        "/v1/settings/models",
        headers={"X-Admin-Key": "admin-secret"},
        json={"reply_model": "qwen3.7-flash", "memory_model": "custom-mem"},
    )
    assert allowed.status_code == 200


def test_runtime_override_rejects_embed_keys(tmp_path):
    client, _, _ = _client(tmp_path, admin_secret="admin-secret")
    # Pydantic forbids unknown keys on RuntimeModelsBody; also store rejects if passed.
    bad = client.put(
        "/v1/admin/runtime-models",
        headers={"X-Admin-Key": "admin-secret"},
        json={"memory_model": "x", "embed_model": "evil"},
    )
    assert bad.status_code == 422


def test_admin_login(tmp_path):
    client, _, _ = _client(tmp_path, admin_secret="admin-secret")
    assert client.post("/v1/admin/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/v1/admin/login", json={"password": "admin-secret"}).json()["ok"] is True

