"""Configure and probe the user-authorized model without printing credentials."""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = 'https://kspmas.ksyun.com/v1'
MODEL = 'deepseek-v4.1-flash'
BACKEND = Path('F:/worktable/research/timem-aml')
BENCH = Path('F:/worktable/research/bench')
OUT = Path(__file__).resolve().parent


def request(method, path, key, payload=None):
    with tempfile.TemporaryDirectory(prefix='timem-model-probe-') as directory:
        directory = Path(directory)
        config = directory / 'request.cfg'
        config.write_text('header = ' + json.dumps('Authorization: Bearer ' + key) + '\n', encoding='utf-8')
        args = ['curl.exe', '-sS', '--max-time', '60', '--config', str(config), '-X', method,
                BASE + path, '-w', '\n%{http_code}']
        if payload is not None:
            body = directory / 'body.json'
            body.write_text(json.dumps(payload), encoding='utf-8')
            args.extend(['-H', 'Content-Type: application/json', '--data-binary', '@' + str(body)])
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=70)
        content, _, status = result.stdout.rpartition('\n')
        status = int(status.strip() or '0')
        try:
            value = json.loads(content)
        except ValueError:
            value = {'raw': content[:1000].replace(key, '[REDACTED]')}
        return status, value


def discover(key):
    status, body = request('GET', '/models', key)
    models = [str(row.get('id', '')) for row in body.get('data', []) if isinstance(row, dict)]
    found = [m for m in models if 'deepseek' in m.lower()]
    print(json.dumps({'models_http': status, 'deepseek_model_ids': found,
                      'error': body.get('error')}, ensure_ascii=False))
    return status, found


def probe(key):
    payload = {'model': MODEL, 'temperature': 0, 'max_tokens': 128,
               'messages': [{'role': 'user', 'content': 'Reply with the single word OK. No reasoning.'}]}
    status, body = request('POST', '/chat/completions', key, payload)
    choice = (body.get('choices') or [{}])[0]
    message = choice.get('message') or {}
    result = {'curl_http': status, 'requested_model': MODEL, 'returned_model': body.get('model'),
              'content': message.get('content'), 'finish_reason': choice.get('finish_reason'),
              'reasoning_length': len(message.get('reasoning_content') or ''), 'error': body.get('error')}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    sys.path.insert(0, str(BENCH))
    from evaluators.aml_score import complete_chat
    try:
        answer = asyncio.run(complete_chat(base_url=BASE, api_key=key, model=MODEL,
                            prompt='Reply with the single word OK. No reasoning.', timeout_sec=30))
        print(json.dumps({'benchmark_httpx_ok': bool(answer), 'answer': answer[:100]}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'benchmark_httpx_ok': False, 'error': str(exc).replace(key, '[REDACTED]')[:500]}, ensure_ascii=False))


def configure(key):
    from dotenv import dotenv_values, set_key
    path = BACKEND / 'data/model_settings.json'
    saved = json.loads(path.read_text(encoding='utf-8'))
    embed_before = json.dumps(saved.get('embed'), sort_keys=True)
    env_embed_before = {k: v for k, v in dotenv_values(BACKEND / '.env').items() if k.startswith('XINGLIU_EMBED_')}
    settings = {
        BACKEND / '.env': {'XINGLIU_API_KEY': key, 'OPENAI_API_KEY': key,
                          'XINGLIU_BASE_URL': BASE, 'OPENAI_BASE_URL': BASE,
                          'XINGLIU_CHAT_MODEL': MODEL, 'XINGLIU_REPLY_MODEL': MODEL},
        BENCH / '.env': {'ARK_API_KEY': key, 'ARK_API_BASE': BASE,
                        'ANSWER_API_KEY': key, 'ANSWER_API_BASE': BASE, 'ANSWER_MODEL': MODEL,
                        'JUDGE_API_KEY': key, 'JUDGE_API_BASE': BASE, 'JUDGE_MODEL': MODEL},
    }
    for env_file, changes in settings.items():
        for name, value in changes.items():
            assert set_key(str(env_file), name, value, quote_mode='never')[0]
    for slot in ['reply', 'memory']:
        saved[slot] = {**saved.get(slot, {}), 'base_url': BASE, 'api_key': key, 'model': MODEL}
    for name, value in {'base_url': BASE, 'api_key': key, 'reply_model': MODEL, 'memory_model': MODEL}.items():
        if name in saved:
            saved[name] = value
    assert json.dumps(saved.get('embed'), sort_keys=True) == embed_before
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
    for env_file, changes in settings.items():
        actual = dotenv_values(env_file)
        assert all(actual[name] == value for name, value in changes.items())
    assert {k: v for k, v in dotenv_values(BACKEND / '.env').items() if k.startswith('XINGLIU_EMBED_')} == env_embed_before
    result = {'configured': True, 'base_url': BASE, 'model': MODEL,
              'targets': ['TiMEM reply', 'TiMEM memory', 'benchmark answer', 'benchmark judge'],
              'embedding_configuration_unchanged': True,
              'files': [str(x) for x in settings] + [str(path)]}
    (OUT / 'model_switch_status.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['discover', 'probe', 'configure'])
    args = parser.parse_args()
    key = sys.stdin.read().strip()
    assert key and '\n' not in key
    if args.mode == 'discover':
        discover(key)
    elif args.mode == 'probe':
        probe(key)
    elif args.mode == 'configure':
        configure(key)
