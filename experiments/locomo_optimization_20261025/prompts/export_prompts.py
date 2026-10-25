"""Read-only AST extraction of actual local prompt templates; no project imports.

Writes only beside this script. Does not call a model, network, or credentials.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
BASE = Path('F:/codex数据库memory')
SCORE = Path('F:/worktable/research/bench/evaluators/aml_score.py')
MANIFEST = []


def export(source, symbol, filename, status, kind='constant'):
    source = Path(source)
    raw = source.read_bytes()
    text = raw.decode('utf-8-sig')
    tree = ast.parse(text)
    found = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            found = node
            break
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == symbol for t in node.targets):
            found = node
            break
    if found is None:
        raise ValueError(f'Missing symbol {symbol} in {source}')
    if kind == 'constant':
        value = ast.literal_eval(found.value)
        assert isinstance(value, str)
        content = value
    elif kind == 'dynamic-template-source':
        value = next(n.value for n in ast.walk(found)
                     if isinstance(n, ast.Return) and isinstance(n.value, ast.JoinedStr))
        content = ast.get_source_segment(text, value) + '\n'
    else:
        content = ast.get_source_segment(text, found) + '\n'
    dest = OUT / filename
    dest.write_text(content, encoding='utf-8', newline='\n')
    MANIFEST.append({
        'attachment': filename, 'status': status, 'kind': kind,
        'source_path': source.as_posix(), 'source_symbol': symbol,
        'source_line': found.lineno, 'source_end_line': found.end_lineno,
        'source_file_sha256': hashlib.sha256(raw).hexdigest(),
        'attachment_sha256': hashlib.sha256(dest.read_bytes()).hexdigest(),
        'extraction': ({
            'constant': 'ast.literal_eval string value, no interpolation',
            'dynamic-template-source': 'ast.get_source_segment exact f-string source expression, no interpolation',
            'function': 'ast.get_source_segment exact function source',
        }[kind]),
    })


def main():
    current = 'current-full-20261025'
    export(SCORE, '_OPEN_ANSWER', '01_CURRENT_answer.txt', current)
    export(SCORE, '_ACCURACY', '02_CURRENT_judge.txt', current)
    for symbol in ('answer_prompt', 'judge_prompt', 'format_memories', 'complete_chat'):
        export(SCORE, symbol, f'03_CURRENT_{symbol}.py', current, 'function')
    prep = BASE / 'locomo_hierarchy_relevance_full_20261025/full_prepare.py'
    export(prep, 'PROMPT_TEMPLATE', '04_CURRENT_joint_rerank.txt', current)
    export(prep, 'build_prompt', '04_CURRENT_build_prompt.py', current, 'function')
    build = BASE / 'locomo_space_l1_l5_20261024/build_hierarchy.py'
    export(build, 'prompt_for', '05_HISTORICAL_hierarchy_prompt_for.py', 'historical-hierarchy-generation', 'function')
    export(build, 'prompt_for', '05_HISTORICAL_hierarchy_fixed_template_source.txt', 'historical-hierarchy-generation', 'dynamic-template-source')
    export(build, 'generate', '05_HISTORICAL_generation_protocol.py', 'historical-hierarchy-generation', 'function')
    specs = [
        ('locomo_hierarchy_relevance_r1_1_20261024/tools/p1_model_screen.py', '06_HISTORICAL_R11_screen.txt'),
        ('locomo_hierarchy_relevance_r1_2_20261024/screen_client.py', '07_HISTORICAL_R12_screen.txt'),
        ('locomo_hierarchy_relevance_r1_3_20261024/stage_b.py', '08_HISTORICAL_R13_joint.txt'),
        ('locomo_hierarchy_relevance_r1_3b_20261024/r13b_run.py', '09_HISTORICAL_R13b_joint.txt'),
    ]
    for src, dest in specs:
        export(BASE / src, 'PROMPT', dest, 'historical-not-current-full')
    c = BASE / 'locomo_temporal_annotation_full_20261023/frozen'
    for src, symbol, dest in [
        ('a0_temporal_evidence.py', '_annotation_line', '10_DYNAMIC_C1_annotation_line.py'),
        ('a0_temporal_evidence.py', 'render_date_evidence', '10_DYNAMIC_C1_render_date_evidence.py'),
        ('arm_c2.py', '_line', '11_DYNAMIC_C2_annotation_line.py'),
        ('arm_c2.py', 'annotate', '11_DYNAMIC_C2_annotate.py'),
        ('arm_c3.py', '_quote', '12_DYNAMIC_C3_quote.py'),
        ('arm_c3.py', '_line', '12_DYNAMIC_C3_annotation_line.py'),
        ('arm_c3.py', 'annotate', '12_DYNAMIC_C3_annotate.py'),
    ]:
        export(c / src, symbol, dest, 'historical-dynamic-evidence-body', 'function')
    w = BASE / 'locomo_temporal_week_20261023/frozen'
    for src, symbol, dest in [
        ('arm_w0.py', 'annotate', '13_DYNAMIC_W0_annotate.py'),
        ('arm_w1.py', 'annotate', '14_DYNAMIC_W1_annotate.py'),
        ('week_ext.py', '_line', '13_DYNAMIC_WEEK_annotation_line.py'),
        ('week_ext.py', 'parse_week_mentions', '13_DYNAMIC_WEEK_parse_mentions.py'),
        ('week_ext.py', 'add_week_anchors', '13_DYNAMIC_WEEK_add_anchors.py'),
    ]:
        export(w / src, symbol, dest, 'historical-dynamic-evidence-body', 'function')
    export(BASE / 'locomo_hierarchy_grounded_20261024/grounded.py', 'compositional_query', '15_CURRENT_compositional_guard.py', current, 'function')
    (OUT / 'PROMPT_SOURCE_MANIFEST.json').write_text(json.dumps({
        'schema': 'prompt-source-manifest-v1', 'method': 'AST extraction, no runtime module import or model calls',
        'extractions': MANIFEST,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'extractions': len(MANIFEST), 'manifest': str(OUT / 'PROMPT_SOURCE_MANIFEST.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
