"""Source evidence keeps native summaries, original turns and tenant boundaries."""
from contextlib import nullcontext
import sys
from types import ModuleType

import pytest

from server.source_evidence import SourceStore, supplement


def test_index_and_retrieve_missing_fact_with_context(tmp_path):
    store = SourceStore(tmp_path / 'sources.sqlite')
    messages = [{'role': 'user', 'content': 'Skincare cost $500.', 'timestamp': 0},
                {'role': 'assistant', 'content': 'What else did you buy?', 'timestamp': 0},
                {'role': 'user', 'content': 'A bag cost $800.', 'timestamp': 0},
                {'role': 'assistant', 'content': 'Unrelated topic.', 'timestamp': 0}]
    store.put('alice', 'l1', 's', messages)
    store.put('alice', 'l1', 's', messages)
    assert len(store.get('alice', 'l1')) == 4
    results = supplement(store, 'alice', 'skincare cost', [{'id': 'l1', 'content': 'Bought a bag for $800.'}], 100)
    assert len(results) == 1
    assert '$500' in results[0]['content'] and '$800' in results[0]['content']
    assert results[0]['content'].startswith('Native memory summary:')
    assert results[0]['id'] == 'l1'
    assert supplement(store, 'bob', 'skincare cost', [], 100) == []


def test_windows_preserve_adjacent_turns_and_are_scoped(tmp_path):
    store = SourceStore(tmp_path / 'sources.sqlite')
    turns = [{'role': 'user', 'content': f'unrelated {i}'} for i in range(12)]
    turns[5]['content'] = 'My skincare cost $500.'
    store.put('alice', 'l1', 's', turns)
    store.put('bob', 'l1', 's', [{'role': 'user', 'content': 'skincare secret'}])
    assert [r['content'] for r in store.windows('alice', 'l1', 'skincare')] == ['unrelated 4', 'My skincare cost $500.', 'unrelated 6']
    assert store.windows('alice', 'l1', 'missing') == []
    with pytest.raises(ValueError):
        store.put('alice', 'l1', 's', [{'role': 'user', 'content': 'different'}])


def test_window_retains_summary_when_other_turn_matches(tmp_path):
    store = SourceStore(tmp_path / 'sources.sqlite')
    turns = [{'role': 'user', 'content': f'unrelated {i}'} for i in range(9)]
    turns[0]['content'] = 'Mindfulness workshop cost $20.'
    turns[7]['content'] = 'Which workshop website should I use?'
    store.put('alice', 'l1', 's', turns)
    results = supplement(store, 'alice', 'workshop website', [{'id': 'l1', 'content': 'Mindfulness workshop cost $20.'}], 10)
    assert '$20' in results[0]['content']
    assert 'Which workshop website' in results[0]['content']


def test_unmatched_native_summary_and_top_k_are_preserved(tmp_path):
    store = SourceStore(tmp_path / 'sources.sqlite')
    store.put('alice', 'l1', 's', [{'role': 'user', 'content': 'unrelated source'}])
    native = [{'id': 'l1', 'content': 'semantic result'}, {'id': 'l2', 'content': 'second result'}]
    assert supplement(store, 'alice', 'no matching keywords', native, 1) == native[:1]


def test_failed_write_is_atomic(tmp_path):
    store = SourceStore(tmp_path / 'sources.sqlite')
    with pytest.raises(KeyError):
        store.put('alice', 'l1', 's', [{'role': 'user', 'content': 'ok'}, {'role': 'user'}])
    assert store.get('alice', 'l1') == []
    store.put('alice', 'l1', 's', [{'role': 'user', 'content': 'ok'}])
    assert len(store.get('alice', 'l1')) == 1


@pytest.mark.asyncio
async def test_engine_add_indexes_generated_native_memory(tmp_path, monkeypatch):
    import server.engine as engine
    monkeypatch.setenv('TIMEM_SOURCE_EVIDENCE', '1')
    monkeypatch.setenv('TIMEM_SOURCE_EVIDENCE_PATH', str(tmp_path / 'sources.sqlite'))
    monkeypatch.setattr(engine, 'memory_model_scope', lambda *args, **kwargs: nullcontext())
    module = ModuleType('timem.workflows.memory_generation')
    class Workflow:
        async def run(self, data):
            return {'success': True, 'generated_memories': [{'id': 'l1'}]}
    module.MemoryGenerationWorkflow = Workflow
    monkeypatch.setitem(sys.modules, module.__name__, module)
    service = engine.EngineMemoryService()
    async def backfill(user_id):
        return None
    monkeypatch.setattr(service, '_backfill', backfill)
    await service.add('alice', 'session', [{'role': 'user', 'content': 'Skincare cost $500.', 'timestamp': 0}])
    assert service.source_store.search('alice', 'skincare') == ['l1']
    assert service.source_store.search('bob', 'skincare') == []


def test_enabled_by_default(monkeypatch, tmp_path):
    from server.engine import EngineMemoryService
    monkeypatch.delenv('TIMEM_SOURCE_EVIDENCE', raising=False)
    monkeypatch.setenv('TIMEM_SOURCE_EVIDENCE_PATH', str(tmp_path / 'sources.sqlite'))
    assert EngineMemoryService().source_store is not None


def test_disabled_when_explicitly_off(monkeypatch):
    from server.engine import EngineMemoryService
    monkeypatch.setenv('TIMEM_SOURCE_EVIDENCE', '0')
    assert EngineMemoryService().source_store is None


@pytest.mark.asyncio
async def test_engine_search_keeps_native_and_adds_source_results(tmp_path, monkeypatch):
    import server.engine as engine
    monkeypatch.setenv('TIMEM_SOURCE_EVIDENCE', '1')
    monkeypatch.setenv('TIMEM_SOURCE_EVIDENCE_PATH', str(tmp_path / 'sources.sqlite'))
    monkeypatch.setattr(engine, 'memory_model_scope', lambda *args, **kwargs: nullcontext())
    module = ModuleType('timem.workflows.memory_retrieval')
    class Workflow:
        @classmethod
        async def create(cls, debug_mode=False):
            return cls()
        async def run(self, data):
            return {'retrieved_memories': [{'id': 'l1', 'content': 'Bought a bag for $800.', 'level': 'L1'}]}
    module.MemoryRetrievalWorkflow = Workflow
    monkeypatch.setitem(sys.modules, module.__name__, module)
    recall = ModuleType('timem.memory_graph.recall')
    async def merge_search_results(records, **kwargs):
        return records
    recall.merge_search_results = merge_search_results
    monkeypatch.setitem(sys.modules, recall.__name__, recall)
    service = engine.EngineMemoryService()
    service.source_store.put('alice', 'l1', 's', [{'role': 'user', 'content': 'Skincare cost $500.'}])
    results = await service.search('alice', 'skincare', 100)
    assert results[0]['id'] == 'l1'
    assert '$500' in results[0]['content'] and '$800' in results[0]['content']
