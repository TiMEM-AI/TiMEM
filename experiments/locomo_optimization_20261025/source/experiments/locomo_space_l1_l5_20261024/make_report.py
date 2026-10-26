import collections, hashlib, json, statistics
from build_hierarchy import ROOT, RUN, MODEL, read, write, now
from run_full import EXPECTED, BASELINE, NAMES

def main():
    output = ROOT/'evaluate_offline'
    state = read(output/'results.partial.json')
    metrics = read(output/'metrics.json')
    audit = read(ROOT/'hierarchy_audit.json')
    assert audit['complete'] and metrics['completed']==861 and not metrics['errors']
    rows = list(state['results'].values())
    assert len(rows)==861 and len({r['question_id'] for r in rows})==861
    assert dict(collections.Counter(r['category'] for r in rows))==EXPECTED
    diagnostics = {'evidence_at_8000_character_cap':sum(len(r['evidence'])>=7999 for r in rows),
        'median_selected_memories':statistics.median(len(r['selected']) for r in rows),
        'single_memory_questions':sum(len(r['selected'])==1 for r in rows)}
    write(output/'evidence_diagnostics.json',diagnostics)
    old_path = ROOT.parent/'locomo_temporal_week_20261023/evaluate/all/rep1/results.partial.json'
    old = [r for r in read(old_path)['results'].values() if r['arm']=='w0']
    assert len(old)==861
    counts = dict(collections.Counter(r['category'] for r in old if r['correct']))
    assert counts==BASELINE
    old_by_id = {r['question_id']:r for r in old}
    transitions = {'fixed':[], 'regressed':[], 'both_correct':[], 'both_wrong':[]}
    for r in rows:
        previous = old_by_id[r['question_id']]['correct']
        key = ('both_correct' if previous else 'fixed') if r['correct'] else ('regressed' if previous else 'both_wrong')
        transitions[key].append(r['question_id'])
    write(output/'paired_comparison.json',{'baseline':'W0 DeepSeek','new':'offline L1-L5 BM25',
        'counts':{k:len(v) for k,v in transitions.items()},'question_ids':transitions})
    actual = collections.Counter(r[part+'_metadata']['returned_model'] for r in rows for part in ['answer','judge'])
    verification = {'completed_at':now(),'questions':861,'unique_questions':861,
        'actual_returned_models':dict(actual),'hierarchy_complete':audit['complete'],
        'old_w0_source_sha256':hashlib.sha256(old_path.read_bytes()).hexdigest(),
        'all_evidence_in_same_conversation':all(x['conv']==r['question_id'].split('#')[0] for r in rows for x in r['selected']),
        'selected_levels':metrics['evidence_levels'], 'no_L0_during_evaluation':True,
        'cloud_recall_used':False, 'new_answer_count':sum('answer' in r for r in rows),
        'new_judge_count':sum('judge' in r for r in rows)}
    assert verification['all_evidence_in_same_conversation']
    write(output/'verification.json',verification)
    lines = ['# LoCoMo 本地 L1–L5 全量测试', '',
        f'- 批次：`{RUN}`；完成时间：{verification["completed_at"]}。',
        f'- 861 题均重新答题和判分；模型均为 `{MODEL}`，温度 0。',
        '- 证据：冻结 L0 经本地时间分组与 DeepSeek 整合生成 L1–L5。测试时不读 L0。',
        '- 检索：按对话隔离的本地 BM25；五层均可参与，删除内容重复的命中；最多 12 条，8,000 字符。',
        '- 本次是用户选择的离线测试，没有调用太忆空间逐题召回，也不是原生 TiMEM 层级整合实验。',
        '- 问题与金标准没有用于记忆生成；金标准仅用于判分。', '',
        '| 类型 | 本次正确/总数 | 本次准确率 | W0 DeepSeek 基线 | 变化（百分点） |',
        '|---|---:|---:|---:|---:|']
    for cat in ['2','4','1','3']:
        m = metrics['categories'][cat]
        lines.append(f"| {NAMES[cat]} | {m['correct']}/{m['total']} | {m['accuracy']:.2%} | {m['baseline_accuracy']:.2%} | {m['delta_pp']:+.2f} |")
    lines.append(f"| 总计（按题数加权） | {metrics['correct']}/861 | {metrics['accuracy']:.2%} | {729/861:.2%} | {metrics['delta_pp']:+.2f} |")
    lines.extend(['', '## 解释范围', '',
        'W0 基线使用原始对话证据与原来的检索、时间标注流程；本次改为整合后的 L1–L5 和本地 BM25。分差同时包含记忆压缩与检索链路的变化，不能单独归因于层级记忆，也不能作为太忆空间云端召回准确率。',
        '', '## 完整性与覆盖', '',
        f"- 原始消息 {audit['source_turns']} 条，覆盖 10 段对话，分配无遗漏和重复。",
        f"- 各层生成数量：{json.dumps(audit['levels'],ensure_ascii=False)}。",
        f"- 实际入选证据层级计数：{json.dumps(metrics['evidence_levels'],ensure_ascii=False)}。",
        f"- 无证据问题：{metrics['no_evidence']}；请求失败：{len(metrics['errors'])}。",
        f"- 模型响应实际返回名称：{json.dumps(dict(actual),ensure_ascii=False)}。",
        f"- 相较 W0：原错现对 {len(transitions['fixed'])} 题，原对现错 {len(transitions['regressed'])} 题；逐题清单见 `evaluate_offline/paired_comparison.json`。",
        '- 只有一个子节点的父节点保留子节点全文；这些条目在检索打包时按内容去重。',
        '', '## 证据预算诊断', '',
        f"{diagnostics['evidence_at_8000_character_cap']}/861 题触及 8,000 字符证据上限；每题入选记忆中位数为 {diagnostics['median_selected_memories']} 条，其中 {diagnostics['single_memory_questions']} 题仅包含一条记忆。当前打包器按整条记忆排序后保留前缀，长摘要可能占据大部分预算。尚未逐题核验正确事实是否恰好落在截断部分，因此这只是需要优先排查的机制，不能据此判定它解释了全部退步。",
        '', '## 云端接入核查', '',
        '原有云端清单为 219 条（L1 118、L2 52、L3 36、L4 11、L5 2），带测试标识的记录仅覆盖 conv-26。用户确认这是 deepseek harness 的部分导入，并授权从冻结 L0 补齐。',
        '普通创建接口会重新提炼内容；单条验证发现“昨天”被错误换算到当前日期。结构化接口返回成功但该记录无法通过详情、列表或召回读到。CSV 导入的 summary 字段完整保留内容，仅移除换行，因此云端后续应验证并使用该字段，不能直接信任改写后的 content。',
        '云端积分不足以完成全部导入和 861 次逐题召回，用户选择先执行本地离线测试。仅建立了少量接入核查记录，未开展批量导入，原有记忆未删除。',
        '', '## 复现文件', '',
        '- `hierarchy_plan.json`：时间分组、来源与父子关系。',
        '- `generated_memories.json`：实际整合记忆、生成模型、请求哈希。',
        '- `hierarchy_audit.json`：来源覆盖与层级审计。',
        '- `evaluate_offline/protocol.json`：冻结测试参数和模板哈希。',
        '- `evaluate_offline/rows/`：逐题检索证据、答案、判分与模型元数据。',
        '- `evaluate_offline/metrics.json`、`verification.json`：汇总及完整性检查。',
        '- `run_full.py --retrieval offline`：断点续跑；禁止在同一输出目录改变协议。', ''])
    (ROOT/'RESULTS.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'report':str(ROOT/'RESULTS.md'),'correct':metrics['correct'],'accuracy':metrics['accuracy']},ensure_ascii=False))

if __name__=='__main__':main()
