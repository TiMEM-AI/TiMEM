import collections,csv,hashlib,json,math,random,statistics
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,'F:/worktable/research/bench')
from evaluators.aml_score import format_memories
import tiktoken
def read(p): return json.loads(p.read_text(encoding='utf-8'))
manifest=read(ROOT/'manifest.json'); questions={r['question_id']:r for r in manifest['questions']}
scores={a:read(ROOT/a/'aml_score.json') for a in ('baseline','variant')}
rows={a:{r['question_id']:r for r in scores[a]['results']} for a in scores}
hits={a:{r['question_id']:r for r in read(ROOT/a/'aml_search.json')['results']} for a in scores}
assert len(questions)==300
for a in scores:
    assert set(rows[a])==set(hits[a])==set(questions)
    assert scores[a]['answer_model']==scores[a]['judge_model']=='deepseek-v4.1-flash'
    assert all(r['method']=='correct_wrong' for r in rows[a].values())
labels={'2':'时间','1':'多跳','4':'单跳','3':'推理'}
metrics=[]
for cat in ['2','1','4','3','all']:
    ids=[k for k,q in questions.items() if cat=='all' or q['category']==cat]
    b=sum(bool(rows['baseline'][k]['correct']) for k in ids)
    v=sum(bool(rows['variant'][k]['correct']) for k in ids)
    metrics.append({'category':cat,'label':labels.get(cat,'总计'),'n':len(ids),'baseline_correct':b,'variant_correct':v,'baseline_pct':b/len(ids)*100,'variant_pct':v/len(ids)*100,'delta_pp':(v-b)/len(ids)*100})
gains=[k for k in questions if not rows['baseline'][k]['correct'] and rows['variant'][k]['correct']]
losses=[k for k in questions if rows['baseline'][k]['correct'] and not rows['variant'][k]['correct']]
discord=len(gains)+len(losses)
p=min(1.,2*sum(math.comb(discord,i) for i in range(min(len(gains),len(losses))+1))/(2**discord)) if discord else 1.
rng=random.Random(1022);differences=[int(rows['variant'][k]['correct'])-int(rows['baseline'][k]['correct']) for k in questions]
bootstrap=sorted(sum(rng.choices(differences,k=300))/3 for _ in range(10000))
encoder=tiktoken.get_encoding('cl100k_base')
tokens={a:[len(encoder.encode(format_memories(hits[a][k]['data'],question=q['question'],options=q.get('options')))) for k,q in questions.items()] for a in hits}
source_coverage=sum(any(r['id'].startswith('src:') for r in h['data']) for h in hits['variant'].values())
summary={'complete':True,'metrics':metrics,'wrong_to_right':len(gains),'right_to_wrong':len(losses),'net':len(gains)-len(losses),'mcnemar_exact_p':p,
    'paired_question_bootstrap_95ci_delta_pp':[bootstrap[250],bootstrap[9749]],'source_coverage':source_coverage,
    'empty_search':{a:sum(not h['data'] for h in hits[a].values()) for a in hits},
    'judge_parse_errors':{a:sum(r['method']=='judge_parse_error' for r in rows[a].values()) for a in rows},
    'mean_evidence_tokens':{a:statistics.mean(v) for a,v in tokens.items()},
    'median_grounding_ms':statistics.median(h['source_grounding_ms'] for h in hits['variant'].values()),
    'provenance':read(ROOT/'experiment.json'),'selection_sha256':hashlib.sha256((ROOT/'manifest.json').read_bytes()).hexdigest()}
(ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
with (ROOT/'paired_questions.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=['question_id','sample_id','category','question','gold','baseline_correct','variant_correct','baseline_answer','variant_answer','baseline_tokens','variant_tokens'])
    writer.writeheader()
    for index,(k,q) in enumerate(questions.items()):
        writer.writerow({key:q.get(key) for key in ('question_id','sample_id','category','question','gold')}|{'baseline_correct':rows['baseline'][k]['correct'],'variant_correct':rows['variant'][k]['correct'],'baseline_answer':rows['baseline'][k]['generated'],'variant_answer':rows['variant'][k]['generated'],'baseline_tokens':tokens['baseline'][index],'variant_tokens':tokens['variant'][index]})
lines=['# TiMem 原文时间证据优化：300 题成对 smoke','',
'完整完成：两组各 300 题，同一问题、同一原始记忆库、同一当前答题/判分模型 deepseek-v4.1-flash，temperature=0。',
'','| 类别 | 题数 | 修改前 | 修改后 | 差值（百分点） |','|---|---:|---:|---:|---:|']
for m in metrics:
    lines.append(f"| {m['label']} | {m['n']} | {m['baseline_correct']}/{m['n']} = {m['baseline_pct']:.2f}% | {m['variant_correct']}/{m['n']} = {m['variant_pct']:.2f}% | {m['delta_pp']:+.2f} |")
lines+=['',f'错转对 {len(gains)} 题，对转错 {len(losses)} 题，净变化 {len(gains)-len(losses):+d} 题。',
f'探索性配对统计：McNemar 精确双侧 p={p:.4g}；按题重采样的净提升 95% 区间为 [{bootstrap[250]:.2f}, {bootstrap[9749]:.2f}] 个百分点。该区间不覆盖模型重新运行的随机性，问题也存在对话内相关性，不能替代多次复测。','',
'## 结果判断','',
'本轮多跳、单跳和推理有所改善，时间类下降 8 个百分点。总分净增 7 题，但配对统计未显示显著提升，尚未实现时间和多跳共同改善的目标。建议保留为实验补丁，先分析时间回退题，再决定是否启用。续跑期间没有继续调整参数或补丁。',
'','## 改了什么','',
'新增 server/source_evidence.py，并完成 AML Add/Search 接入补丁 app.staged.py：按原始消息时间保存事实叶，检索时补齐同时间段的相邻问答。时间问题优先使用原始时间来源，其他问题结合少量原有摘要。证据模块已用于本轮实验；API 接入补丁尚未替换运行中的 app.py。已有摘要/向量、回答提示及判分规则未重新生成或调整。',
'','## 为什么选这个方向','',
'论文强调细粒度事实与时间层级，详见 [论文第 3、4.6 节](https://arxiv.org/html/2601.02845v2)。本地 299 个输入块中有 236 块跨时间；原来的块首日锚点会误解后续会话中的相对日期。具体分析、其他优化方向及测试协议见 [RESEARCH.md](RESEARCH.md)。',
'','## 评测边界与完整性','',
'这是一轮成对的检索后处理消融：每题先调用修改前服务重新检索，实验组共享同一次候选，再调用 server/source_evidence.py 中的原文证据层，因此能控制检索随机性。并非两次独立端到端重建/重跑。8 项模块测试及 1 项使用假记忆服务的 Add/Search 集成测试通过；本轮没有上线或重启服务。',
'',
'定向抽样覆盖 10 段对话，含全部 77 道多跳题；本次总分不代表 861 题全量得分，也不直接与旧的混合模型 74.91% 相减。新原文索引只含历史 Add 的 5,882 条消息，没有引入金标、任务类别或官方证据标注。',
'',f"新证据覆盖 {source_coverage}/300；空检索：{summary['empty_search']}；判分解析失败：{summary['judge_parse_errors']}。",
f"平均证据 token：修改前 {statistics.mean(tokens['baseline']):.0f}，修改后 {statistics.mean(tokens['variant']):.0f}。新增本地证据处理延迟中位数 {summary['median_grounding_ms']:.1f} ms；这是共享基线检索之上的增量耗时。",'',
'## 改善与回退样例','']
for label,ids in [('错转对',gains),('对转错',losses)]:
    lines.append('### '+label);lines.append('')
    for k in ids[:5]:
        q=questions[k];b=rows['baseline'][k];v=rows['variant'][k]
        lines.extend([f"- `{k}`：{q['question']}",f"  金标：{q['gold']}",f"  前：{b['generated']}",f"  后：{v['generated']}"])
    lines.append('')
lines+=['## 交付文件','',
'- `paired_questions.csv`：全部逐题答案、判分及证据长度。',
'- `baseline/`、`variant/`：完整检索证据、判分原文和问题清单。',
'- `summary.json`：统计和实验来源。',
'- `app.diff`、`app.before.py`、`source_evidence.py`：补丁与回退备份。',
'- 运行时回退：设置 `TIMEM_SOURCE_EVIDENCE=0` 后重启服务。原文索引保留以便复查。']
(ROOT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k not in ('provenance',)},ensure_ascii=False,indent=2))
