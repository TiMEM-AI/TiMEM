"""Memory event graph: schedule, idempotency, and search merge. No live Neo4j."""

import asyncio
from datetime import datetime

from timem.memory_graph.build import ingest_l2_rows, l2_children
from timem.memory_graph.qa import GraphQueryError, answer_graph
from timem.memory_graph.search import rrf_fuse_seed_ids, search_graph
from timem.memory_graph.recall import (
    collect_l2_ids,
    merge_graph_first,
    merge_search_results,
    parse_drill_request,
    rows_for_l2,
)
from timem.memory_graph.schedule import safe_schedule_after_l3, schedule_after_l3, wait_inflight
from timem.memory_graph.store import DisabledMemoryGraph, _query_tokens, neo4j_settings


class _Task:
    def __init__(self, layer, user_id="user-1", expert_id="assistant"):
        self.layer = layer
        self.user_id = user_id
        self.expert_id = expert_id
        self.time_window = {"start_time": datetime(2026, 10, 17)}


class FakeStore:
    enabled = True

    def __init__(self):
        self.episodes = {}
        self.writes = 0

    async def find_l2(self, user_id, expert_id, l2_id):
        return self.episodes.get(l2_id)

    async def write_episode(self, episode, facts):
        self.writes += 1
        self.episodes[episode["l2_id"]] = {
            "content_hash": episode["content_hash"],
            "episode_id": episode["episode_id"],
        }


def _run(coro):
    return asyncio.run(coro)


def test_non_l3_is_not_scheduled():
    async def _check():
        store = FakeStore()
        key = await schedule_after_l3(_Task("L2"), {"child_memory_ids": ["a"]}, store=store, enabled=True)
        assert key is None
        assert store.writes == 0

    _run(_check())


def test_l3_schedules_child_l2():
    async def _check():
        store = FakeStore()
        calls = []

        async def load(memory_id):
            return {"id": memory_id, "level": "L2", "content": "Alice met Bob"}

        async def complete(prompt):
            calls.append(prompt)
            return '{"name":"见面","subject":"Alice","object":"Bob"}'

        key = await schedule_after_l3(
            _Task("L3"),
            {"child_memory_ids": ["l2-1"]},
            store=store,
            complete=complete,
            load_memory=load,
            enabled=True,
        )
        assert key == "user-1:assistant"
        await wait_inflight("user-1", "assistant")
        assert store.writes == 1
        assert calls

    _run(_check())


def test_empty_l2_finishes_without_write():
    async def _check():
        store = FakeStore()
        rows = await l2_children({"child_memory_ids": []}, lambda memory_id: None)
        stats = await ingest_l2_rows(
            user_id="user-1",
            expert_id="assistant",
            report_date="2026-10-17",
            rows=rows,
            store=store,
            complete=_unused_complete,
        )
        assert stats["empty"] == 1
        assert store.writes == 0

    _run(_check())


def test_extract_failure_does_not_raise():
    async def _check():
        store = FakeStore()

        async def complete(prompt):
            raise RuntimeError("llm down")

        stats = await ingest_l2_rows(
            user_id="user-1",
            expert_id="assistant",
            report_date="2026-10-17",
            rows=[{"id": "l2-1", "content": "Alice met Bob"}],
            store=store,
            complete=complete,
        )
        assert stats["failed"] == 1
        assert store.writes == 0

        async def boom(*args, **kwargs):
            raise RuntimeError("schedule broke")

        import timem.memory_graph.schedule as schedule

        original = schedule.schedule_after_l3
        schedule.schedule_after_l3 = boom
        try:
            await safe_schedule_after_l3(_Task("L3"), {})
        finally:
            schedule.schedule_after_l3 = original

    _run(_check())


def test_same_l2_skips_and_changed_content_rewrites():
    async def _check():
        store = FakeStore()
        calls = []

        async def complete(prompt):
            calls.append(1)
            return '{"name":"见面","subject":"Alice"}'

        row = {"id": "l2-1", "content": "Alice met Bob"}
        first = await ingest_l2_rows(
            user_id="user-1", expert_id="assistant", report_date="2026-10-17",
            rows=[row], store=store, complete=complete,
        )
        second = await ingest_l2_rows(
            user_id="user-1", expert_id="assistant", report_date="2026-10-17",
            rows=[row], store=store, complete=complete,
        )
        changed = await ingest_l2_rows(
            user_id="user-1", expert_id="assistant", report_date="2026-10-17",
            rows=[{"id": "l2-1", "content": "Alice met Bob again"}],
            store=store, complete=complete,
        )
        assert first["done"] == 1
        assert second["skipped"] == 1
        assert changed["done"] == 1
        assert len(calls) == 2

    _run(_check())


def test_merge_puts_new_graph_memories_first_and_cuts():
    vector = [
        {"id": "v1", "content": "vector one"},
        {"id": "v2", "content": "vector two"},
    ]
    graph = [
        {"id": "v1", "content": "vector one"},
        {"id": "g1", "content": "from graph"},
        {"id": "g1", "content": "from graph"},
    ]
    merged = merge_graph_first(vector, graph, top_k=2)
    assert [row["id"] for row in merged] == ["g1", "v1"]


def test_drill_opens_at_most_three_and_verbatim_adds_l1():
    parsed = parse_drill_request('{"open":["a","b","c","d","e"],"verbatim":true}')
    assert parsed["open"] == ["a", "b", "c"]
    assert parsed["verbatim"] is True
    nodes = [{"name": "a", "source_l2_ids": ["l2-1", "l2-2", "l2-3", "l2-4"]}]
    assert collect_l2_ids(nodes, parsed["open"]) == ["l2-1", "l2-2", "l2-3"]

    async def load(memory_id):
        if memory_id == "l2-1":
            return {"id": "l2-1", "level": "L2", "content": "session", "child_memory_ids": ["l1-1", "l3-x"]}
        if memory_id == "l1-1":
            return {"id": "l1-1", "level": "L1", "content": "raw line"}
        return {"id": memory_id, "level": "L3", "content": "not l1"}

    async def _check():
        brief = await rows_for_l2(["l2-1"], load, include_l1=False)
        full = await rows_for_l2(["l2-1"], load, include_l1=True)
        assert [row["id"] for row in brief] == ["l2-1"]
        assert [row["id"] for row in full] == ["l2-1", "l1-1"]

    _run(_check())


def test_disabled_graph_keeps_vector_order():
    vector = [
        {"id": "v1", "content": "one"},
        {"id": "v2", "content": "two"},
        {"id": "v3", "content": "three"},
    ]

    async def _check():
        merged = await merge_search_results(
            vector,
            user_id="user-1",
            expert_id="assistant",
            question="anything",
            top_k=2,
            store=DisabledMemoryGraph(),
            wait=False,
        )
        assert [row["id"] for row in merged] == ["v1", "v2"]

    _run(_check())


def test_chinese_question_keeps_two_character_tokens():
    tokens = _query_tokens("昨天的会议为什么改期")
    assert "会议" in tokens
    assert len(tokens) <= 12


def test_neo4j_settings_require_uri_and_password(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "")
    monkeypatch.setenv("NEO4J_PASSWORD", "")

    class _Config:
        def get_config(self, key):
            return {"uri": "", "password": "", "user": "neo4j", "database": "neo4j"}

    import timem.utils.config_manager as config_manager

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _Config())
    assert neo4j_settings() is None


class RankStore:
    enabled = True

    def __init__(self):
        self.hops = []

    async def lexical_seed_ids(self, user_id, expert_id, query, limit):
        return ["episode-1", "other"]

    async def fulltext_seed_ids(self, user_id, expert_id, query, limit):
        return ["episode-1"]

    async def vector_seed_ids(self, user_id, expert_id, vector, limit):
        return ["entity-1", "episode-1"]

    async def expand(self, user_id, expert_id, seed_ids, hops, limit):
        self.hops.append((hops, list(seed_ids)))
        return {
            "nodes": [
                {"id": "episode-1", "name": "酸奶碗", "label": "MemoryEpisode", "source_l2_ids": ["l2-1"]},
                {"id": "entity-1", "name": "用户", "label": "MemoryEntity", "source_l2_ids": ["l2-1"]},
            ],
            "edges": [{
                "id": "e1",
                "type": "HAS_OBJECT",
                "from": "episode-1",
                "to": "entity-1",
                "fact": "用户当天吃了酸奶碗",
            }],
        }


def test_rrf_puts_the_node_found_by_every_path_first():
    fused = rrf_fuse_seed_ids(
        ["episode-1", "other"],
        ["entity-1", "episode-1"],
        ["episode-1"],
        limit=2,
    )
    assert fused[0] == "episode-1"


def test_graph_search_returns_subgraph_without_answer():
    async def _check():
        store = RankStore()
        view = await search_graph(
            store,
            user_id="user-1",
            expert_id="assistant",
            query="酸奶碗",
            query_vector=[1.0, 0.0],
        )
        assert view["answer"] is None
        assert view["nodes"][0]["id"] == "episode-1"
        assert store.hops[0][0] == 1
        assert store.hops[0][1][0] == "episode-1"
        assert "content" not in view

    _run(_check())


def test_graph_qa_answers_from_relations_without_reading_l2():
    async def _check():
        loaded = []

        async def complete(prompt):
            return "用户当天吃了酸奶碗"

        async def load(memory_id):
            loaded.append(memory_id)
            return {"id": memory_id, "level": "L2", "content": "不应被读到"}

        view = await answer_graph(
            RankStore(),
            user_id="user-1",
            expert_id="assistant",
            question="吃了什么",
            complete=complete,
            load_memory=load,
        )
        assert view["answer"] == "用户当天吃了酸奶碗"
        assert loaded == []

    _run(_check())


def test_graph_qa_opens_l2_then_returns_an_answer():
    async def _check():
        calls = []

        async def complete(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return '{"open":["酸奶碗"],"verbatim":false}'
            return "根据原文，用户吃了酸奶碗"

        async def load(memory_id):
            return {"id": memory_id, "level": "L2", "content": "午餐是酸奶碗"}

        view = await answer_graph(
            RankStore(),
            user_id="user-1",
            expert_id="assistant",
            question="原文里吃了什么",
            complete=complete,
            load_memory=load,
        )
        assert view["answer"] == "根据原文，用户吃了酸奶碗"
        assert "午餐是酸奶碗" in calls[1]
        assert view["answer"] != "午餐是酸奶碗"

    _run(_check())


def test_graph_qa_rejects_hops_outside_1_to_3():
    async def _check():
        try:
            await answer_graph(
                RankStore(),
                user_id="user-1",
                expert_id="assistant",
                question="吃了什么",
                hops=4,
                complete=_unused_complete,
            )
        except GraphQueryError as exc:
            assert "1" in str(exc)
            return
        raise AssertionError("expected GraphQueryError")

    _run(_check())


def test_embed_failure_still_writes_the_episode():
    async def _check():
        store = FakeStore()

        async def complete(prompt):
            return '{"name":"见面","subject":"Alice"}'

        async def embed(text):
            raise RuntimeError("embed down")

        stats = await ingest_l2_rows(
            user_id="user-1",
            expert_id="assistant",
            report_date="2026-10-17",
            rows=[{"id": "l2-1", "content": "Alice met Bob"}],
            store=store,
            complete=complete,
            embed=embed,
        )
        assert stats["done"] == 1
        assert store.writes == 1

    _run(_check())


async def _unused_complete(prompt):
    raise AssertionError("extract should not run")
