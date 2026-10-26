import json
from pathlib import Path
import httpx
from dotenv import dotenv_values

root = Path('F:/worktable/research/timem-aml')
cfg = dotenv_values(root / '.env')
headers = {'X-API-Key': cfg['TIMEM_API_KEY']}
with httpx.Client(trust_env=False, timeout=240) as client:
    health = client.get('http://127.0.0.1:8000/health')
    print('health', health.status_code, flush=True)
    assert health.status_code == 200
    qdrant = client.get('http://localhost:16333/collections')
    print('qdrant', qdrant.status_code, flush=True)
    assert qdrant.status_code == 200
    body = {'request_id': 'smoke1022a', 'user_id': 'smoke1022', 'session_id': 'smoke1022',
            'messages': [{'role': 'user', 'content': 'Alice is a nurse in Hangzhou.', 'timestamp': 1683507600000}]}
    added = client.post('http://127.0.0.1:8000/aml/add', headers=headers, json=body)
    print('add', added.status_code, flush=True)
    assert added.status_code == 200 and added.json().get('success') is True
    found = client.post('http://127.0.0.1:8000/aml/search', headers=headers,
                        json={'query': 'In what city does Alice work?', 'user_id': 'smoke1022', 'top_k': 10})
    print('search', found.status_code, flush=True)
    assert found.status_code == 200
    memories = found.json()['data']
    print(json.dumps({'returned': len(memories), 'evidence': [{'level': m.get('level'), 'content': m.get('content')} for m in memories]}, ensure_ascii=False), flush=True)
    assert memories and any('Hangzhou' in m.get('content', '') for m in memories)
