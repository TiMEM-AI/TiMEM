import collections, hashlib, json, math, statistics
from datetime import datetime,timezone,timedelta
from pathlib import Path
import grounded as g

NAMES={'2':'时间','4':'单跳','1':'多跳','3':'推理'}

def paired(a,b,ids):
    ids=list(ids);fixed=[];regressed=[];same_correct=0;same_wrong=0
    for qid in ids:
        x=a[qid]['correct'];y=b[qid]['correct']
        if y and not x:fixed.append(qid)
        elif x and not y:regressed.append(qid)
        elif x:same_correct+=1
        else:same_wrong+=1
    n=len(fixed)+len(regressed)
    p=min(1.,2*sum(math.comb(n,k) for k in range(min(len(fixed),len(regressed))+1))/2**n) if n else 1.
    return {'total':len(ids),'lower_correct':sum(a[q]['correct'] for q in ids),'all_correct':sum(b[q]['correct'] for q in ids),
        'lower_accuracy':sum(a[q]['correct'] for q in ids)/len(ids),'all_accuracy':sum(b[q]['correct'] for q in ids)/len(ids),
        'delta_pp':100*(len(fixed)-len(regressed))/len(ids),'fixed':fixed,'regressed':regressed,
        'fixed_count':len(fixed),'regressed_count':len(regressed),'both_correct':same_correct,'both_wrong':same_wrong,
        'exact_mcnemar_two_sided_p':p}

def main():
    phase=g.ROOT/'full'
    summary=g.read(phase/'summary.json');assert summary['complete']
    outcomes={arm:g.read(phase/arm/'results.json')['results'] for arm in ['lower_only','all_levels']}
    metrics={arm:g.read(phase/arm/'metrics.json') for arm in outcomes}
    a=outcomes['lower_only'];b=outcomes['all_levels'];assert len(a)==len(b)==861 and set(a)==set(b)
    for arm in outcomes:
        assert metrics[arm]['completed']==861 and not metrics[arm]['errors']
        assert metrics[arm]['actual_models']=={g.MODEL:1722}
    frozen=g.read(g.ROOT/'preregister_full.json')
    for name,expected in frozen['code_sha256'].items():assert hashlib.sha256((g.ROOT/name).read_bytes()).hexdigest()==expected
    old_data=g.read(g.OLD/'generated_memories.json')['records']
    expected=g.read(g.OLD/'evaluate_offline/protocol.json')['hierarchy_sha256']
    assert hashlib.sha256(json.dumps(old_data,sort_keys=True).encode()).hexdigest()==expected
    source_plan=g.read(g.OLD/'hierarchy_plan.json')
    assert hashlib.sha256(Path(source_plan['source_path']).read_bytes()).hexdigest()==source_plan['source_sha256']
    dev=set(frozen['development_40_ids']);remaining=set(a)-dev
    comparisons={'full_861':paired(a,b,a),'excluding_development_821':paired(a,b,remaining)}
    comparisons['categories']={c:paired(a,b,[qid for qid in a if a[qid]['category']==c]) for c in NAMES}
    comparisons['by_conversation']={conv:paired(a,b,[qid for qid in a if qid.startswith(conv+'#')])
        for conv in sorted({qid.split('#')[0] for qid in a})}
    g.write(phase/'paired_comparison.json',comparisons)
    store=g.read(g.ROOT/'store.json');same=0;changed=0;cross=0;duplicate=0;levels=collections.Counter();shares=collections.Counter()
    details={};response_ids=set();smoke_ids=set()
    for arm,rows in outcomes.items():
        tokens=[];sizes=[];upper_windows=0
        for qid,row in rows.items():
            request=g.read(Path(row['evidence_file']));assert g.sha(request['evidence'])==row['evidence_sha256']
            ids=request['diagnostics']['fact_ids'];duplicate+=len(ids)-len(set(ids))
            cross+=sum(store['facts'][fid]['conv']!=qid.split('#')[0] for fid in ids)
            assert len(request['windows'])<=g.RULES['max_windows']
            upper_windows+=request['navigation']['upper_windows'];sizes.append(len(ids))
            tokens.append(row['answer_metadata']['usage'].get('prompt_tokens',0))
            for key in ['answer_metadata','judge_metadata']:
                response_ids.add(row[key]['response_id']);shares[key]+=bool(row[key]['shared'])
            if arm=='all_levels':
                for w in request['windows']:levels.update(w['route_levels'])
        details[arm]={'median_answer_prompt_tokens':statistics.median(tokens),'median_evidence_facts':statistics.median(sizes),
                     'upper_navigation_windows':upper_windows}
    for qid in a:
        if a[qid]['evidence_sha256']==b[qid]['evidence_sha256']:
            same+=1
            assert a[qid]['answer']==b[qid]['answer'] and a[qid]['correct']==b[qid]['correct']
            assert a[qid]['answer_metadata']['response_id']==b[qid]['answer_metadata']['response_id']
        else:changed+=1
    for arm in outcomes:
        for row in g.read(g.ROOT/'smoke'/arm/'results.json')['results'].values():
            for key in ['answer_metadata','judge_metadata']:smoke_ids.add(row[key]['response_id'])
    assert not response_ids&smoke_ids and cross==0 and duplicate==0
    cache=list((phase/'response_cache').glob('*.json'))
    verification={'finished_at':g.now(),'model':g.MODEL,'two_complete_861_question_arms':True,
        'same_evidence_pairs':same,'changed_evidence_pairs':changed,'unique_valid_cached_responses':len(cache),
        'smoke_response_ids_reused_in_full':0,'identical_evidence_answer_disagreements':0,
        'cross_conversation_facts':cross,'duplicate_fact_ids_in_requests':duplicate,
        'upper_navigation_window_level_counts':dict(levels),'arm_details':details,'shared_response_uses':dict(shares),
        'historical_source_snapshot_unchanged':True,'frozen_code_unchanged':True,'runtime_L0':False,'cloud':False}
    g.write(phase/'verification.json',verification)
    c=comparisons['full_861'];held=comparisons['excluding_development_821']
    confirmation=None
    if (g.ROOT/'confirm/summary.json').exists() and g.read(g.ROOT/'confirm/summary.json')['complete']:
        confirm_rows={arm:g.read(g.ROOT/'confirm'/arm/'results.json')['results'] for arm in outcomes}
        assert set(confirm_rows['lower_only'])==set(confirm_rows['all_levels'])
        ids=set(confirm_rows['lower_only']);assert len(ids)==312
        result=paired(confirm_rows['lower_only'],confirm_rows['all_levels'],ids)
        first_affected=paired(a,b,ids)
        confirm_ids=set();drifts={}
        for arm,rows in confirm_rows.items():
            assert g.read(g.ROOT/'confirm'/arm/'metrics.json')['completed']==312
            assert not g.read(g.ROOT/'confirm'/arm/'metrics.json')['errors']
            answer_drift=0;judge_drift=0
            for qid,row in rows.items():
                original=outcomes[arm][qid]
                assert row['evidence_sha256']==original['evidence_sha256']
                answer_drift+=row['answer']!=original['answer']
                judge_drift+=row['answer']==original['answer'] and row['correct']!=original['correct']
                for key in ['answer_metadata','judge_metadata']:confirm_ids.add(row[key]['response_id'])
            drifts[arm]={'answers_changed':answer_drift,'judge_label_changed_with_identical_answer':judge_drift}
        assert not confirm_ids&response_ids and not confirm_ids&smoke_ids
        confirmation={'comparison':result,'first_run_affected_questions':first_affected,
            'net_gain_normalized_by_861_pp':100*(result['fixed_count']-result['regressed_count'])/861,
            'frozen_evidence_identical_to_first_run':True,'fresh_response_ids':True,'drift':drifts,
            'limits':'312 input-changed questions only, not a second complete 861-question accuracy; no pooling paired p-values'}
        g.write(g.ROOT/'confirm/analysis.json',confirmation)
    if c['delta_pp']>0 and c['exact_mcnemar_two_sided_p']<0.05:
        verdict='本轮受控比较支持：在当前 LoCoMo 题集上，条件式高层导航优于同配置的 L1/L2 检索。'
    elif c['delta_pp']>0:
        verdict='联合方案的单次分数略高，但配对差异尚不足以支持稳定优于 L1/L2 的结论。'
    elif c['delta_pp']==0:
        verdict='联合方案与 L1/L2 总分相同，本轮没有观察到净准确率收益。'
    else:
        verdict='联合方案在本轮受控比较中低于 L1/L2，不能把“五层共同调用”本身视为准确率提升保证。'
    local=datetime.fromisoformat(verification['finished_at']).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    lines=['# 层级导航改造与 L1/L2 受控全量对照','',f'完成时间：{local}（北京时间）。{verdict}','',
        '## 两组各 861 题','',
        '| 类型 | L1/L2 正确/总数 | L1/L2 准确率 | L1–L5 正确/总数 | L1–L5 准确率 | 差值（百分点） |',
        '|---|---:|---:|---:|---:|---:|']
    for cat,name in NAMES.items():
        r=comparisons['categories'][cat]
        lines.append(f"| {name} | {r['lower_correct']}/{r['total']} | {r['lower_accuracy']:.2%} | {r['all_correct']}/{r['total']} | {r['all_accuracy']:.2%} | {r['delta_pp']:+.2f} |")
    lines.append(f"| 总计 | {c['lower_correct']}/861 | {c['lower_accuracy']:.2%} | {c['all_correct']}/861 | {c['all_accuracy']:.2%} | {c['delta_pp']:+.2f} |")
    lines.extend(['',f"原错现对 {c['fixed_count']} 题，原对现错 {c['regressed_count']} 题；双侧精确 McNemar p={c['exact_mcnemar_two_sided_p']:.4f}。这是本题集的配对辅助检验，不构成跨数据集、跨模型的稳定性保证。",'',
        '## 剔除本轮开发样本后的 821 题','',
        f"L1/L2：{held['lower_correct']}/821（{held['lower_accuracy']:.2%}）；L1–L5：{held['all_correct']}/821（{held['all_accuracy']:.2%}）；差值 {held['delta_pp']:+.2f} 个百分点；修复 {held['fixed_count']} 题、退步 {held['regressed_count']} 题，p={held['exact_mcnemar_two_sided_p']:.4f}。",
        '这 821 题没有用于本轮规则开发；题集在本项目早先实验中已使用，不能称为全新独立测试集。','',
        '## 两轮开发 smoke','',
        '| 版本 | L1/L2 | L1–L5 | 说明 |','|---|---:|---:|---|'])
    for label,key,explain in [('第一版','smoke_v1','20 窗口，保护 16 个，无条件扩展'),('冻结版','smoke_v2','24 窗口，保护 20 个，有条件扩展')]:
        s=frozen[key];lines.append(f"| {label} | {s['lower_only']['correct']}/40 | {s['all_levels']['correct']}/40 | {explain} |")
    lines.extend(['', '两版使用同一 40 题开发样本；策略和代码在全量前冻结，完整开发记录保留。两版窗口设置不同，不能把其分数变化只归因于一个参数。','',
        '## 修改与实测诊断','',
        '- 高层保留摘要用于导航，答题证据确定性继承实际子事实；父层时间窗口不覆盖事实观察时间。',
        '- 原始 L1/L2 快照未改，建立 7,157 个规范化事实的独立索引。L3–L5 引用这些事实，避免多轮摘要再丢失细节。',
        '- 16,950 个高层摘要单元中，16,884 个达到子事实链接门槛，66 个未链接单元不直接输出为事实。431 个底层事实没有强词面上层链接；这些是词面覆盖线索，不等于已确认的语义丢失数量。',
        '- 上轮 40 题证据中，完全重复事实行比例中位数约 18.7%；新请求内完全重复事实 ID 为 0，同义改写的重复仍可能存在。',
        '- 直接日期查询保持低层证据；其他查询通过通用词面意图启发式、信息词、来源链接和新会话条件筛选高层增补，不使用任务类别、题目 ID 或金标准路由。该启发式仍可能误判自然语言意图。',
        f"- 两组最大窗口均为 {g.RULES['max_windows']}，联合方案保护前 {g.RULES['protected_flat_windows']} 个低层窗口。W0 格式化函数、117,760 token 上限及新增注释 8,000 字符预算一致。",
        f"- {same} 题两组证据完全相同，共享本轮响应后答案及判分完全一致；{changed} 题证据发生变化。全量没有读取历史或 smoke 的模型响应缓存。",
        f"- 实际高层导航窗口层级计数：{json.dumps(dict(levels),ensure_ascii=False)}。高层参与路由，不强求每题都塞入各层完整摘要。",
        f"- L1/L2 的答题输入 token 中位数为 {details['lower_only']['median_answer_prompt_tokens']:.0f}；联合方案为 {details['all_levels']['median_answer_prompt_tokens']:.0f}。",
        '- 两组各完成 861 次答案记录与 861 次判分记录，无失败请求、无跨对话事实；完全相同输入共享响应，因此实际唯一模型响应少于记录总数。', '',
        '## 与历史成绩的关系','',
        f"原 W0 为 729/861（84.67%），上一轮上层时间改造为 694/861（80.60%）；本轮联合方案为 {c['all_correct']}/861（{c['all_accuracy']:.2%}）。",
        '历史 W0 还使用原始对话检索通道；本批是同一个 L1/L2 摘要事实库上的受控导航消融。与历史的变化同时包含证据颗粒度、检索方式、去重等修改，只有本轮两组的比较可直接评估当前高层导航的增益。', '',
        '## 结论边界','',verdict,
        '该结果来自一个本地 LoCoMo 实验、固定模型和同一判分器；不是太忆空间云端召回成绩。高层继承关系能保证索引中保留子事实，不能恢复此前生成 L1/L2 时已经压缩掉的原始信息，也不意味着每条生成事实都经过原文语义蕴含验证。', '',
        '## 复现与后续定位','',
        '- `IMPLEMENTATION.md`：设计、两轮开发和控制条件。',
        '- `NEXT_STEPS.md`：尚未实施的保真 L1 与语义增补方案。',
        '- `store.json`、`index.sqlite`：事实、继承关系、路由单元和实际索引。',
        '- `preregister_full.json`、`full/protocol.json`：冻结规则与代码哈希。',
        '- `requests/`、`full/*/rows/`：实际证据、窗口来源、答案、判分与用量。',
        '- `full/paired_comparison.json`：两组修复、退步、分类及逐对话清单。',
        '- `full/verification.json`：缓存隔离、来源、重复及对话隔离验证。',
        '后续若继续开发，应使用新开发样本或新会话验证，以本轮退步清单定位机制，但不要按本轮全量答案逐题修补并再当作独立验证。',''])
    if confirmation:
        r=confirmation['comparison'];first=confirmation['first_run_affected_questions']
        text=['## 一次独立复测：312 道输入改变题','',
            '由于首次净增只有 5 题，额外预先约定只复测一次，选择全部证据发生变化的 312 题，不按对错筛选。规则和证据完全未改，模型响应缓存与首次全量及 smoke 隔离。', '',
            '| 批次 | L1/L2 | L1–L5 | 修复 | 退步 | 净增题数 |', '|---|---:|---:|---:|---:|---:|',
            f"| 首次全量中的这 312 题 | {first['lower_correct']}/312 | {first['all_correct']}/312 | {first['fixed_count']} | {first['regressed_count']} | {first['fixed_count']-first['regressed_count']:+d} |",
            f"| 独立复测 | {r['lower_correct']}/312 | {r['all_correct']}/312 | {r['fixed_count']} | {r['regressed_count']} | {r['fixed_count']-r['regressed_count']:+d} |", '',
            f"复测双侧精确 McNemar p={r['exact_mcnemar_two_sided_p']:.4f}；净差折算到 861 题分母为 {confirmation['net_gain_normalized_by_861_pp']:+.2f} 个百分点。549 道输入相同题在配对比较中始终为零差异，不需要为衡量算法差异而重复调用。这里没有重跑完整 861 题，因此不构造第二轮完整准确率，也不把两次检验当成独立题目合并。", '',
            f"同样证据下的回答变化及相同回答判分变化：{json.dumps(confirmation['drift'],ensure_ascii=False)}。该波动也说明应谨慎解释小幅单次增益。",'']
        index=lines.index('## 两轮开发 smoke');lines[index:index]=text
    if confirmation and confirmation['comparison']['delta_pp']<=0 and c['delta_pp']>0:
        verdict='首次全量仅小幅领先，独立复测未保持正向收益；当前不能确认这版联合方案稳定优于 L1/L2。'
        lines[2]=f'完成时间：{local}（北京时间）。{verdict}'
        marker=lines.index('## 结论边界');lines[marker+2]=verdict
    (g.ROOT/'RESULTS.md').write_text('\n'.join(lines),encoding='utf-8')
    g.write(g.ROOT/'final_summary.json',{'verdict':verdict,'full':c,'excluding_development':held,'categories':comparisons['categories'],'verification':verification,'confirmation':confirmation})
    print(json.dumps({'verdict':verdict,'full':{k:v for k,v in c.items() if k not in ['fixed','regressed']},'heldout_821':{k:v for k,v in held.items() if k not in ['fixed','regressed']},'report':str(g.ROOT/'RESULTS.md')},ensure_ascii=False))

if __name__=='__main__':main()
