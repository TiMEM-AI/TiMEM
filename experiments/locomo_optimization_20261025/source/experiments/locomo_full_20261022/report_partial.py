import collections
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from report_locomo import BENCH, OUT, RUN, RUN_ID, CATEGORIES, read, group

data = read(RUN / 'aml_score.partial.json')
manifest = read(RUN / 'aml_questions.json')
search = read(RUN / 'aml_search.json')
expected = collections.Counter(str(q['category']) for q in manifest['questions'])
groups = group(data['results'])
baseline = [read(BENCH / 'reports' / run / 'aml_score.json') for run in ['run5', 'run6', 'run7']]
paired = [r for r in data['results'] if r['question_id'].startswith('conv-26#')]
import statistics
base_groups = [group(x['results']) for x in baseline]
base_mean = {c: statistics.mean(g[c]['accuracy'] for g in base_groups) for c, _ in CATEGORIES}
base_total = statistics.median(x['accuracy'] for x in baseline)
summary = {'run_id': RUN_ID, 'status': 'incomplete_insufficient_balance',
           'time': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
           'expected_questions': len(manifest['questions']), 'scored': len(data['results']),
           'searched': len(search['results']), 'correct': data['correct'], 'partial_accuracy': data['accuracy'],
           'unscored': len(manifest['questions']) - len(data['results']),
           'categories': groups, 'expected_by_category': dict(expected),
           'conv26': {'total': len(paired), 'correct': sum(r['correct'] for r in paired),
                      'accuracy': sum(r['correct'] for r in paired) / len(paired), 'categories': group(paired)},
           'baseline': {'category_mean': base_mean, 'total_median': base_total},
           'blocker': {'http_status': 402, 'message': 'Insufficient Balance', 'balance_cny_latest': '-0.15'},
           'resume_command': 'python -B -u main.py aml-search f1022b --score'}
(OUT / 'partial_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
lines = ['# LoCoMo-Refined 全量测试：中断报告', '',
         '**状态：未完成。DeepSeek 返回 HTTP 402 / Insufficient Balance。不能将以下部分成绩视为 861 题全量成绩。**', '',
         f"运行 ID：`{RUN_ID}`。已写入 299/299 个数据块，落库 L1 数量为 299。检索完成 {len(search['results'])}/861 题，判分完成 {len(data['results'])}/861 题，剩余 {summary['unscored']} 题。", '',
         '| 类别 | 已判分/全量 | 正确数 | 已判分部分准确率 |', '|---|---:|---:|---:|']
for c, name in CATEGORIES:
    g = groups[c]
    lines.append(f"| {name} | {g['total']}/{expected[c]} | {g['correct']} | {g['accuracy']:.2%} |")
lines += [f"| **合计** | **{data['total']}/861** | **{data['correct']}** | **{data['accuracy']:.2%}** |", '',
          '## 已完成的同范围比较：conv-26', '',
          '| 类别 | 本轮正确/题数 | 本轮准确率 | 旧基线分类均值 | 差值（百分点） |', '|---|---:|---:|---:|---:|']
for c, name in CATEGORIES:
    g = summary['conv26']['categories'][c]
    lines.append(f"| {name} | {g['correct']}/{g['total']} | {g['accuracy']:.2%} | {base_mean[c]:.2%} | {100*(g['accuracy']-base_mean[c]):+.2f} |")
pc = summary['conv26']
lines += [f"| **合计** | **{pc['correct']}/{pc['total']}** | **{pc['accuracy']:.2%}** | **{base_total:.2%}** | **{100*(pc['accuracy']-base_total):+.2f}** |", '',
          '旧基线分类为 run5–run7 均值，总分为三次中位数；本轮仅一次。全量已完成部分的题目构成不同，不能直接据此判断相对基线的优化收益。', '',
          '## 恢复与改动', '',
          '原始断点位于 bench/reports/f1022b，充值后运行本目录 resume_final.ps1。该命令调用现有 aml-search f1022b --score，读取已保存的检索与判分断点，跳过已完成的 747 题，不执行 Add。', '',
          '运行稳定性修复：server/app.py 增加 asyncio 导入，将同步 filter_owned 调用移入 asyncio.to_thread，避免数据库锁等待阻塞事件循环。归属校验逻辑不变。补丁为 runtime_fix.diff，原文件为 app.before.py。', '',
          '本地进程使用 NO_PROXY=127.0.0.1,localhost,::1，并使用短运行 ID 适配数据库字段长度。失败的 full-20261022-1550 和 f1022a 不计入本次成绩；f1022b 首次在判分前因缺少 import 失败，修复后复用成功 Add 的幂等缓存重新开始全部题目。', '']
(OUT / 'PARTIAL_REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
