import collections, hashlib, inspect, json, statistics
from pathlib import Path
from datetime import datetime, timezone, timedelta
from upper_w0 import ROOT, PREVIOUS, W0, BENCH, MODEL, UPPER, read, write, now, canonical, sha
from run_eval import metrics
from evaluators.aml_score import format_memories

def main():
    output=ROOT/'full/upper_w0'
    current=read(output/'results.json')['results']
    full=read(output/'metrics.json')
    storage=read(ROOT/'storage_audit.json');format_audit=read(ROOT/'format_audit.json')
    smoke=read(ROOT/'smoke_summary.json')
    assert full['completed']==861 and not full['errors'] and storage['passed'] and format_audit['passed']
    assert full['actual_models']=={MODEL:1722}
    assert len(current)==861 and all('correct' in r for r in current.values())
    previous=read(PREVIOUS/'evaluate_offline/results.partial.json')['results']
    original={r['question_id']:r for r in read(W0/'evaluate/all/rep1/results.partial.json')['results'].values() if r['arm']=='w0'}
    assert set(current)==set(previous)==set(original)
    questions=read(W0/'frozen/questions.json')['questions']
    previous_m=metrics(previous,questions)
    original_correct=collections.Counter(r['category'] for r in original.values() if r['correct'])
    original_total=collections.Counter(r['category'] for r in original.values())
    comparisons={}
    for name,baseline in [('previous_8k_offline',previous),('original_W0',original)]:
        pairs=collections.defaultdict(list)
        for qid,row in current.items():
            old=baseline[qid]['correct'];new=row['correct']
            key=('both_correct' if old else 'fixed') if new else ('regressed' if old else 'both_wrong')
            pairs[key].append(qid)
        comparisons[name]={'counts':{k:len(v) for k,v in pairs.items()},'question_ids':dict(pairs)}
    write(output/'paired_comparison.json',comparisons)
    old_store=read(PREVIOUS/'generated_memories.json')['records'];store=read(ROOT/'memories.json')['records']
    lower_unchanged=sum(canonical(store[k])==canonical(v) for k,v in old_store.items() if v['level'] not in UPPER)
    assert lower_unchanged==671
    assert hashlib.sha256((BENCH/'evaluators/aml_score.py').read_bytes()).hexdigest()==read(W0/'experiment.json')['hashes']['bench_aml_score']
    actual_levels=collections.Counter();rendered_annotations=0;input_tokens=[];cross_conv=0
    full_ids=set();smoke_ids=set()
    for row in current.values():
        request=read(Path(row['evidence_file']))
        assert sha(request['evidence'])==row['evidence_sha256']
        assert not request['diagnostics']['parent_window_used_as_upper_fact_anchor']
        actual_levels.update(x['level'] for x in request['diagnostics']['evidence_row_times'])
        rendered_annotations+=request['diagnostics']['annotations_rendered']
        cross_conv+=sum(s['conv']!=row['question_id'].split('#')[0] for s in row['selected'])
        input_tokens.append(row['answer_metadata']['usage'].get('prompt_tokens',0))
        full_ids.update(row[k].get('response_id') for k in ['answer_metadata','judge_metadata'])
    for arm in ['budget_control','upper_w0']:
        for row in read(ROOT/'smoke'/arm/'results.json')['results'].values():
            smoke_ids.update(row[k].get('response_id') for k in ['answer_metadata','judge_metadata'])
    shared=(full_ids&smoke_ids)-{None}
    assert not shared and cross_conv==0
    verification={'completed_at':now(),'questions':861,'fresh_answers':861,'fresh_judgements':861,
        'model_responses':full['actual_models'],'unchanged_lower_nodes':lower_unchanged,
        'upper_nodes':565,'cross_conversation_evidence':cross_conv,'smoke_full_shared_response_ids':len(shared),
        'actual_evidence_rows_by_level':dict(actual_levels),'rendered_annotations':rendered_annotations,
        'answer_prompt_tokens_median':statistics.median(input_tokens),'answer_prompt_tokens_max':max(input_tokens),
        'formatter_matches_W0':True,'raw_L0_read_at_evaluation':False,'cloud_recall':False,
        'code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['upper_w0.py','run_eval.py']}}
    write(output/'verification.json',verification)
    local_time=datetime.fromisoformat(verification['completed_at']).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    lines=['# L3–L5 W0 时间存储改造：smoke 与全量结果','',
        f"完成时间：{local_time}（北京时间）。本次为本地离线实验，答题与判分均为 `{MODEL}`，温度 0。",'',
        '## 全量 861 题','',
        '| 类型 | 本次正确/总数 | 本次准确率 | 上轮离线（总证据限 8k） | 相比上轮（百分点） | 原 W0 | 相比 W0（百分点） |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for cat in ['2','4','1','3']:
        row=full['categories'][cat];p=previous_m['categories'][cat];w=original_correct[cat]/original_total[cat]
        lines.append(f"| {row['name']} | {row['correct']}/{row['total']} | {row['accuracy']:.2%} | {p['accuracy']:.2%} | {(row['accuracy']-p['accuracy'])*100:+.2f} | {w:.2%} | {(row['accuracy']-w)*100:+.2f} |")
    w=sum(r['correct'] for r in original.values())/861
    lines.append(f"| 总计（按题数加权） | {full['correct']}/861 | {full['accuracy']:.2%} | {previous_m['accuracy']:.2%} | {(full['accuracy']-previous_m['accuracy'])*100:+.2f} | {w:.2%} | {(full['accuracy']-w)*100:+.2f} |")
    lines.extend(['','## 固定 40 题 smoke','',
        '每类 10 题，选题依据为类别、固定随机种子与时间注释覆盖；不使用历史正确性。最终 smoke 的结果如下，后续全量没有复用这些模型响应。','',
        '| 方案 | 时间 | 单跳 | 多跳 | 推理 | 总计 |','|---|---:|---:|---:|---:|---:|'])
    groups=[('上轮同题结果',smoke['previous_8000_char_run']),('仅恢复 W0 证据预算',smoke['scores']['budget_control']),('上层存储与时间格式改造',smoke['scores']['upper_w0'])]
    for name,m in groups:
        values=[f"{m['categories'][c]['correct']}/10" for c in ['2','4','1','3']]
        lines.append('| '+name+' | '+' | '.join(values)+f" | {m['correct']}/40（{m['accuracy']:.2%}） |")
    lines.extend(['','## 已修改并验证的内容','',
        '- L1/L2 的 671 个节点完整不变；L3–L5 的 565 个节点改为事实级观察时间、来源与注释，节点窗口另存为聚合元数据。',
        f"- 上层共有 {storage['stats']['facts']} 个事实单元；日历注释绑定 {storage['stats']['calendar_bindings']} 条、周注释绑定 {storage['stats']['week_bindings']} 条。这些数字包含同一来源在不同高层节点中的复用，不代表新增了这么多原始事件。",
        '- 仅在存储构建阶段读取原始观察，核验短语、日期与字符区间。测试阶段不读取 L0；没有用原始全文替换摘要。',
        '- BM25 索引内容、打分参数、对话隔离、排序及最多 12 个去重节点不变，全部 861 题的旧检索和旧证据均成功逐字复现。',
        '- W0 时间规则、独立注释块、周级精度、查询路由及答题和判分模板保持一致。主证据格式化函数与原 W0 文件哈希完全相同。',
        '- 8,000 字符恢复为“新增注释预算”，主证据采用 W0 原有 token 预算；上一轮将它设为全部证据上限的错误已修正。',
        f"- {full['completed']} 题完成，无失败请求、无跨对话证据；smoke 与全量模型响应 ID 无复用。实际证据行层级计数：{json.dumps(dict(actual_levels),ensure_ascii=False)}。",
        f"- 本轮实际呈现时间注释 {rendered_annotations} 条；答题请求输入 token 中位数 {statistics.median(input_tokens):.0f}，最大 {max(input_tokens)}。",'',
        '## 解释范围与剩余限制','',
        '原 W0 的 84.67% 使用原始对话检索通道与时间注释；本轮使用摘要型 L1–L5 和本地 BM25。格式化函数和时间字段语义恢复一致，不表示正文或检索通道与原 W0 完全相同，也不是太忆空间云端召回成绩。',
        '本轮同时修正证据预算并改变上层呈现。40 题预算对照提供了初步线索，但没有运行预算对照组的全量测试，不能精确量化两项修改各自贡献。',
        '本次没有重新生成事实，也不能恢复早先摘要压缩时丢失的信息。观察时间虽可在后代真实时间集合中验证，仍不等价于逐事件证明模型原先给每条摘要选的日期都正确。注释的短语与同日来源已验证，但未宣称唯一事件对齐。',
        '初版 smoke 后的结构检查发现 last week/last weekend 子串绑定问题；已修正、归档初版、按相同题目复跑，最终全量使用修正版。', '',
        '## 后续排查建议','',
        '若目标是继续接近原 W0，应固定本轮预算和候选排序，分别检查：正确事实是否已在 L1/L2 摘要中丢失；高层事实的日期是否来自实际子事实；跨多个节点的证据是否被长摘要与重复内容淹没。下一轮可考虑保留高层摘要用于导航，并确定性继承带来源的子事实作为答题证据，再做受控对照，而不是继续让模型自行重写时间。', '',
        '实现细节见 `IMPLEMENTATION.md`；逐题证据见 `requests/upper_w0/`；逐题答题与判分见 `full/upper_w0/rows/`；相较两个基线的修复与退步题目见 `full/upper_w0/paired_comparison.json`。',''])
    (ROOT/'RESULTS.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'report':str(ROOT/'RESULTS.md'),'correct':full['correct'],'accuracy':full['accuracy'],'verification':verification},ensure_ascii=False))

if __name__=='__main__':main()
