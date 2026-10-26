import asyncio
import json
import os
import sys
from pathlib import Path
import httpx
from dotenv import dotenv_values

OUT = Path(__file__).resolve().parent
BASE = 'https://kspmas.ksyun.com/v1'
MODEL = 'deepseek-v4.1-flash'
sys.path.insert(0, 'F:/worktable/research/bench')
from runners.aml_run import _score_endpoints, _score_question_row


async def main():
    cfg = dotenv_values('F:/worktable/research/timem-aml/.env')
    headers = {'X-API-Key': cfg['TIMEM_API_KEY']}
    result = {'base_url': BASE, 'model': MODEL}
    async with httpx.AsyncClient(trust_env=False, timeout=90) as client:
        response = await client.get('http://127.0.0.1:8000/v1/settings/models', headers=headers)
        response.raise_for_status()
        saved = response.json()
        result['loaded_slots'] = {slot: {'model': saved[slot]['model'], 'base_url': saved[slot]['base_url']}
                                  for slot in ['reply', 'memory']}
        assert all(saved[slot]['model'] == MODEL and saved[slot]['base_url'] == BASE for slot in ['reply', 'memory'])
        async def verify(slot):
            response = await client.post('http://127.0.0.1:8000/v1/settings/models/verify',
                                         headers=headers, json={'target': slot})
            return slot, {'http': response.status_code, **response.json()}
        result['timem_verify'] = dict(await asyncio.gather(verify('reply'), verify('memory')))
    endpoints = _score_endpoints()
    assert endpoints['answer_model'] == endpoints['judge_model'] == MODEL
    assert endpoints['answer_base'] == endpoints['judge_base'] == BASE
    fixture = {'dataset_id': 'locomo_refined', 'question_id': 'connectivity-only',
               'question': 'In which city does Alice work?', 'gold': 'Hangzhou', 'category': '4'}
    score = await _score_question_row(fixture, '- Alice works in Hangzhou.', **endpoints)
    result['benchmark_answer_judge'] = {'answer': score['generated'], 'correct': score['correct'],
                                        'judge_raw': score.get('judge_raw'), 'method': score['method']}
    result['all_ok'] = all(x.get('ok') is True for x in result['timem_verify'].values()) and score['correct'] is True
    (OUT / 'model_connectivity.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


asyncio.run(main())
