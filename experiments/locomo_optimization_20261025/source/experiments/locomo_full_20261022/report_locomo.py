"""Summarize the existing AML reports without changing evaluation behavior."""
import argparse
import collections
import csv
import hashlib
import json
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BENCH = Path('F:/worktable/research/bench')
BACKEND = Path('F:/worktable/research/timem-aml')
OUT = Path(__file__).resolve().parent
RUN_ID = 'f1022b'
RUN = BENCH / 'reports' / RUN_ID
CATEGORIES = [('2', '时间'), ('4', '单跳'), ('1', '多跳'), ('3', '推理')]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def group(rows):
    result = {}
    for category, name in CATEGORIES:
        chosen = [r for r in rows if str(r['category']) == category]
        correct = sum(r.get('correct') is True for r in chosen)
        result[category] = dict(name=name, correct=correct, total=len(chosen),
                                accuracy=correct / len(chosen) if chosen else None)
    return result


def progress():
    status = {'time': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')}
    for filename, label in [('aml_add.json', 'add'), ('aml_search.json', 'search'),
                            ('aml_score.partial.json', 'score'), ('aml_score.json', 'final')]:
        path = RUN / filename
        if not path.exists():
            continue
        try:
            data = read(path)
        except (ValueError, OSError):
            status[label] = 'writing'
            continue
        if label == 'add':
            status[label] = {'chunks': data['chunks'],
                             'failed': sum(x.get('success') is not True for x in data['adds']),
                             'by_sample': dict(collections.Counter(x['user_id'].rsplit(':', 1)[-1] for x in data['adds']))}
        else:
            rows = data.get('results', [])
            status[label] = {'count': len(rows)}
            if label != 'search':
                status[label].update(correct=data['correct'], accuracy=data['accuracy'])
                status[label]['by_sample'] = dict(collections.Counter(r['question_id'].split('#')[0] for r in rows))
                status[label]['judge_parse_errors'] = sum(r.get('method') == 'judge_parse_error' for r in rows)
            else:
                status[label]['empty'] = sum(not r.get('data') for r in rows)
    print(json.dumps(status, ensure_ascii=False))


def finish():
    final = read(RUN / 'aml_score.json')
    manifest = read(RUN / 'aml_questions.json')
    added = read(RUN / 'aml_add.json')
    searched = read(RUN / 'aml_search.json')
    rows = final['results']
    expected_ids = {q['question_id'] for q in manifest['questions']}
    scored_ids = {r['question_id'] for r in rows}
    searched_ids = {r['question_id'] for r in searched['results']}
    assert len(expected_ids) == 861
    assert scored_ids == searched_ids == expected_ids
    assert len(rows) == len(scored_ids)
    assert len(searched['results']) == 861
    assert final['total'] == len(rows)
    assert added['chunks'] == 299
    assert all(x.get('success') is True for x in added['adds'])
    assert all(str(r['category']) in dict(CATEGORIES) for r in rows)
    assert final['correct'] == sum(r.get('correct') is True for r in rows)
    assert abs(final['accuracy'] - final['correct'] / final['total']) < 1e-12
    checkpoint = read(OUT / 'checkpoint.747.score.json')
    previous = {r['question_id']: r for r in checkpoint['results']}
    previous_search = {r['question_id'] for r in read(OUT / 'checkpoint.748.search.json')['results']}
    by_id = {r['question_id']: r for r in rows}
    assert len(previous) == 747
    assert all(by_id[qid] == row for qid, row in previous.items()), 'Previously scored rows changed'
    resumed = [r for r in rows if r['question_id'] not in previous]
    assert len(resumed) == 114
    old_correct = sum(r['correct'] is True for r in previous.values())
    new_correct = sum(r['correct'] is True for r in resumed)
    provenance = {
        'mixed_models': True,
        'l1_generation_model': 'deepseek-chat',
        'original': {'answer_model': 'deepseek-chat', 'judge_model': 'deepseek-chat',
                     'total': 747, 'correct': old_correct, 'accuracy': old_correct / 747,
                     'categories': group(list(previous.values()))},
        'resumed': {'answer_model': 'deepseek-v4.1-flash', 'judge_model': 'deepseek-v4.1-flash',
                    'base_url': 'https://kspmas.ksyun.com/v1', 'total': 114,
                    'correct': new_correct, 'accuracy': new_correct / 114, 'categories': group(resumed)},
        'searches_reused': len(previous_search), 'searches_resumed': len(rows) - len(previous_search),
    }
    baseline = [read(BENCH / 'reports' / run / 'aml_score.json') for run in ['run5', 'run6', 'run7']]
    baseline_groups = [group(b['results']) for b in baseline]
    baseline_mean = {c: statistics.mean(g[c]['accuracy'] for g in baseline_groups) for c, _ in CATEGORIES}
    baseline_total = statistics.median(b['accuracy'] for b in baseline)
    current = group(rows)
    paired = [r for r in rows if r['question_id'].startswith('conv-26#')]
    paired_groups = group(paired)
    paired_correct = sum(r.get('correct') is True for r in paired)
    paired_accuracy = paired_correct / len(paired)
    audit = {'expected': len(expected_ids), 'scored': len(rows), 'searched': len(searched_ids),
             'original_747_scores_unchanged': True, 'resumed_questions': len(resumed),
             'added_chunks': added['chunks'], 'add_failed': 0,
             'empty_searches': sum(not x.get('data') for x in searched['results']),
             'judge_parse_errors': sum(r.get('method') == 'judge_parse_error' for r in rows),
             'methods': dict(collections.Counter(r.get('method') for r in rows))}
    runtime_observations = {'invalid_id_retry_log_entries': 0, 'missing_prompt_log_entries': 0}
    runtime_log = OUT / 'server.v41.out.log'
    if runtime_log.exists():
        with runtime_log.open(encoding='utf-8', errors='replace') as handle:
            for line in handle:
                runtime_observations['invalid_id_retry_log_entries'] += 'Result validation failed: invalid_ids' in line
                runtime_observations['missing_prompt_log_entries'] += 'Prompt not found after reload:' in line
    db_counts = read(OUT / 'database_counts.json')
    audit['database_l1_count'] = sum(x['memories'] for x in db_counts if x['level'] == 'L1')
    assert audit['database_l1_count'] == 299
    config = read(OUT / 'configuration.json')
    audit['source_changes_during_final_run'] = [p for p, h in config['hashes_at_final_start'].items()
                                               if hashlib.sha256((BACKEND / p).read_bytes()).hexdigest() != h]
    assert not audit['source_changes_during_final_run']
    summary = {'run_id': RUN_ID, 'dataset': 'locomo_refined', 'official': False, 'complete': True,
               'total': final['total'], 'correct': final['correct'], 'accuracy': final['accuracy'],
               'categories': current,
               'model_provenance': provenance,
               'runtime_observations': runtime_observations,
               'baseline': {'runs': ['run5', 'run6', 'run7'], 'sample': 'conv-26', 'questions': 87,
                            'total_median': baseline_total, 'category_mean': baseline_mean},
               'delta_full_vs_baseline_pp': 100 * (final['accuracy'] - baseline_total),
               'conv26': {'total': len(paired), 'correct': paired_correct, 'accuracy': paired_accuracy,
                          'categories': paired_groups, 'delta_pp': 100 * (paired_accuracy - baseline_total)},
               'audit': audit}
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    detailed_rows = [{**r,
                     'batch': 'original' if r['question_id'] in previous else 'resumed',
                     'answer_model': 'deepseek-chat' if r['question_id'] in previous else 'deepseek-v4.1-flash',
                     'judge_model': 'deepseek-chat' if r['question_id'] in previous else 'deepseek-v4.1-flash',
                     'search_model': 'deepseek-chat' if r['question_id'] in previous_search else 'deepseek-v4.1-flash'}
                    for r in rows]
    annotated = {**final, 'answer_model': 'mixed', 'judge_model': 'mixed',
                 'model_provenance': provenance, 'results': detailed_rows}
    (OUT / 'score_with_provenance.json').write_text(json.dumps(annotated, ensure_ascii=False, indent=2), encoding='utf-8')
    questions = {q['question_id']: q for q in manifest['questions']}
    with (OUT / 'questions.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        fields = ['question_id', 'category', 'task', 'correct', 'question', 'gold', 'generated', 'search_returned', 'latency_ms', 'method', 'batch', 'answer_model', 'judge_model', 'search_model']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in detailed_rows:
            writer.writerow({'question_id': row['question_id'], 'category': row['category'],
                             'task': dict(CATEGORIES)[str(row['category'])], 'correct': row['correct'],
                             'question': questions[row['question_id']]['question'], 'gold': row.get('gold'),
                             'generated': row.get('generated'), 'search_returned': row.get('search', {}).get('returned'),
                             'latency_ms': row.get('search', {}).get('latency_ms'), 'method': row.get('method'),
                             **{k: row[k] for k in ['batch', 'answer_model', 'judge_model', 'search_model']}})
    lines = ['# LoCoMo-Refined 全量评测（混合模型续跑）', '', f'运行 ID：`{RUN_ID}`。本地非官方答题与判分，断点续跑完成。', '',
             '共 10 段对话、861 题；按运行 ID 隔离数据。299 个数据块与 L1 记忆由 deepseek-chat 生成。前 747 题答题/判分使用 deepseek-chat，余额不足后剩余 114 题按用户要求改用金山云 deepseek-v4.1-flash；答题和判分温度均为 0。合计属于混合模型成绩，不能作为某个单一模型的基线。', '',
             '运行稳定性修复：server/app.py 将同步 filter_owned 调用改为 await asyncio.to_thread(filter_owned, ...)，避免数据库锁等待阻塞事件循环；归属校验逻辑不变。原文件和补丁保存在本目录。进程设置 NO_PROXY 绕过本地代理，运行 ID 缩短以适配数据库字段长度。前面的失败运行不计入本次成绩。', '',
             '| 类别 | 正确/题数 | 本轮全量 | 旧基线分类均值 | 差值（百分点） |',
             '|---|---:|---:|---:|---:|']
    for c, name in CATEGORIES:
        g = current[c]
        lines.append(f"| {name} | {g['correct']}/{g['total']} | {g['accuracy']:.2%} | {baseline_mean[c]:.2%} | {100*(g['accuracy']-baseline_mean[c]):+.2f} |")
    lines.append(f"| **总计** | **{final['correct']}/{final['total']}** | **{final['accuracy']:.2%}** | **{baseline_total:.2%}** | **{100*(final['accuracy']-baseline_total):+.2f}** |")
    lines += ['', '总分按全部题目正确数/总题数计算，不取四类准确率的简单平均。旧基线分类值为 run5–run7 均值，总分为三次中位数。', '',
              '## 模型批次', '', '| 批次 | 答题与判分模型 | 正确/题数 | 准确率 |', '|---|---|---:|---:|',
              f'| 原批次 | deepseek-chat | {old_correct}/747 | {old_correct/747:.2%} |',
              f'| 本次续跑 | deepseek-v4.1-flash | {new_correct}/114 | {new_correct/114:.2%} |', '',
              '两个批次题目不同，不能据此比较模型能力。原有 747 条判分逐项核对保持不变；复用 748 条已保存检索结果，新增 113 条检索。逐题模型来源见 score_with_provenance.json 和 questions.csv。', '',
              '## 同范围比较：conv-26', '', '| 类别 | 本轮正确/题数 | 本轮准确率 | 旧基线分类均值 | 差值（百分点） |', '|---|---:|---:|---:|---:|']
    for c, name in CATEGORIES:
        g = paired_groups[c]
        lines.append(f"| {name} | {g['correct']}/{g['total']} | {g['accuracy']:.2%} | {baseline_mean[c]:.2%} | {100*(g['accuracy']-baseline_mean[c]):+.2f} |")
    lines += [f'| **总计** | **{paired_correct}/{len(paired)}** | **{paired_accuracy:.2%}** | **{baseline_total:.2%}** | **{100*(paired_accuracy-baseline_total):+.2f}** |', '',
              '全量卷与旧基线的样本及类别比例、模型组合不同，因此全量差值不能直接解释为优化收益；conv-26 子集全部使用原 deepseek-chat，具有相同题目范围，但本轮仅一次，仍存在模型及记忆生成波动。', '',
              '## 完整性检查', '', f"写入 {added['chunks']} 个数据块全部成功；861 题均完成检索与判分；空检索 {audit['empty_searches']} 题；判分解析错误 {audit['judge_parse_errors']} 题。", '',
              '## 运行观察', '',
              f"续跑日志包含 {runtime_observations['invalid_id_retry_log_entries']} 条精排 invalid_ids 校验重试记录；现有 relevance_analysis_simple / memory_refiner 模板缺失时使用默认提示。本轮记录这些现象，未调整检索流程。该计数为日志条目数，不代表失败题数。", '',
              f'原始报告目录：`{RUN}`。原始 aml_score.json 顶层模型字段只反映续跑时配置；本目录 score_with_provenance.json 标明混合模型与逐题来源，questions.csv 含逐题答案、判分、证据数量和模型，summary.json 含计算结果。', '']
    (OUT / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    historical = OUT / 'PARTIAL_REPORT.md'
    if historical.exists():
        text = historical.read_text(encoding='utf-8')
        banner = '> 本轮已续跑完成。以下为中断时的历史快照；当前结果见 [完整报告](F:/codex数据库memory/locomo_full_20261022/REPORT.md)。\n\n'
        if not text.startswith('> 本轮已续跑完成'):
            historical.write_text(banner + text, encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--progress', action='store_true')
    args = parser.parse_args()
    progress() if args.progress else finish()
