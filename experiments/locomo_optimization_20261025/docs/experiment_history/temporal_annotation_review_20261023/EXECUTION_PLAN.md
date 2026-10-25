# 时间粒度标注：评审结论与 LoCoMo 全量执行方案

日期：2026-10-23。用途：交给 deepseek harness 实现与验证。本次只做文档评审、代码检查、无网络合成探针和全量离线重放，没有修改后端、原始规格或评测结果，也没有新调用答题/判分模型。

## 1. 结论与执行决策

**方案可行，但“补一个 precision 字段就会提高换算准确率”目前没有证据支持。** 当前 `server/temporal_evidence.py::_annotation_line` 已向模型输出 `precision=day/month/year` 和 `calendar_value`。原规格 17 个用例的现有验证脚本全部通过，但它主要比较 resolved 条目的 value/precision，没有完整检查状态、跨度和去重；新规格主要增加 anchor_date/status、统一字段和契约，不是首次引入粒度信息。

建议执行两项改进，并分开测量：

1. **规格格式实验**：严格按原提议统一标注格式，测它相对已有 A0 是否有实际收益。
2. **推荐修订实验**：修复完整短语识别、跨度、去重等确定性缺陷，并为每条日期注释补上对应原句，使模型能分辨“哪件事的哪一个日期”。保留原句中的计划、否定和条件，不生成“建议答案”或未经证实的事件断言。

第一轮不加星期/周末推算、不调检索、不重建记忆、不改答题与 judge 提示，也不引入 LLM 路由。若修订版仍无收益，再把“按问题选择相关注释”作为独立实验；不要一开始混入 top-3 截断、事件重排等改动。

原规格逐节批注见 [temporal-annotation-spec.reviewed.md](F:/codex数据库memory/temporal_annotation_review_20261023/temporal-annotation-spec.reviewed.md)。其中【必须修改】属于正确性或验证要求，【建议】属于待实测的优化假设。

## 2. 现有证据：不能忽略的实验边界

最新 300 题 fresh 记录如下：

| 类别 | n | V1：原文证据、无注释 | V2：当前 A0 | 净变化 |
|---|---:|---:|---:|---:|
| 时间 | 100 | 71 | 70 | -1 |
| 多跳 | 77 | 51 | 51 | 0 |
| 单跳 | 98 | 87 | 85 | -2 |
| 推理 | 25 | 21 | 21 | 0 |
| 总计 | 300 | 230（76.67%） | 227（75.67%） | -3 |

复核发现：

- 时间类实际是 **6 道错转对、7 道对转错**；现有 REPORT 第 9 节的“+5/-6”需要在后续报告中纠正。
- 时间题 `conv-44#q0001` 两组答案都是 `Last week.`，judge 却分别判对、判错。单跳题 `conv-48#q0127` 的相同答案也出现判定翻转。不能将全部分差归因于注释。
- 同样的旧 V1 输入重跑，答案变化 187/300，判分翻转 53/300。可以确认生成/判分行为不稳定；仅凭这些记录不能断言远端模型版本已经变更。
- weekday 答案变好不等于 A0 已解析 weekday；当前 A0 明确不支持该规则，可能是其他上下文变化或模型波动。
- 当前 trace 实际只保存 annotation_block 文本，没有完整的结构化跨度记录。不要沿用“跨度已全部审计”的过强表述。

结果依据：[最新报告](F:/codex数据库memory/locomo_temporal_v2_20261023/REPORT.md)、[逐题原始结果](F:/codex数据库memory/locomo_temporal_v2_20261023/fresh/aml_score.partial.json)、[离线输入对照](F:/codex数据库memory/locomo_temporal_v2_20261023/offline/input_diff.json)。本次不改历史产物。

## 3. 全量范围与保护目标

完整 LoCoMo-Refined 共 **861 题：时间 167、多跳 77、单跳 581、推理 36**。使用现有完整题单，不再按 smoke 300 抽样，不丢弃周、星期、时长或无注释题。

本次已用 `f1022b` 的冻结候选、只读原文快照和当前 A0 做全量离线重放，未调用模型：

| 类别 | 题数 | 当前主问路由命中 | 当前 A0 实际改变证据 |
|---|---:|---:|---:|
| 时间 | 167 | 126 | 125 |
| 多跳 | 77 | 0 | 0 |
| 单跳 | 581 | 2 | 2 |
| 推理 | 36 | 0 | 0 |

两道单跳是 `conv-47#q0088`（何时计划从 Toronto/Vancouver 返回）与 `conv-48#q0080`（何时买宠物蛇）；它们确实在语义上询问日期。当前分别增加 1,374、907 字符。

因此，新版要检查 **692 道未触发的非时间题输入不变**，并对这两道受影响单跳做真实效果验收。若修订规则进一步使其中某题回退，记录实际结果。**不允许根据 category/qid 排除这两题，也不能承诺任意未来非时间题都完全不受影响。** 分类标签只进入离线统计，不进入生产路由或注释函数。

## 4. 冻结输入与版本

| 用途 | 路径 |
|---|---|
| 用户原规格，只读 | `F:\worktable\doc\temporal-annotation-spec.md` |
| 后端 | `F:\worktable\research\timem-aml` |
| Benchmark | `F:\worktable\research\bench` |
| 全量问题 | `F:\worktable\research\bench\reports\f1022b\aml_questions.json` |
| 全量旧候选 | `F:\worktable\research\bench\reports\f1022b\aml_search.json` |
| 已有原文快照 | `F:\codex数据库memory\locomo_temporal_v2_20261023\frozen\aml_sources.sqlite` |
| 无注释 V1 冻结代码 | `F:\codex数据库memory\locomo_smoke300_20261022\source_evidence.py` |
| 当前 A0 冻结副本 | `F:\codex数据库memory\locomo_temporal_v2_20261023\patch\temporal_evidence.py` 与 `source_evidence.v2.py` |
| 现有 runner 参考 | `F:\codex数据库memory\locomo_temporal_v2_20261023\tools` |
| 建议新输出目录 | `F:\codex数据库memory\locomo_temporal_annotation_full_20261023` |

原规格 SHA-256：`da18e4c5067c10f86d1d55ebe844fcf0da27b20aef551fe410d63fdd9a40bc21`。

当前 A0 模块及其 patch 副本 SHA-256：`a3cc21b988046114a6038d5d3a2b86b4199ac79991859373296c22141cbd2473`。

接手时先复制所需代码与数据到新的 frozen 子目录并记录 hash。SQLite 使用只读连接；若快照来源是活动库，用 backup API，不仅复制可能含 WAL 的主文件。记录 source_turns/source_requests 的排序内容 hash，核对 5,882 turns、299 requests。不得为本实验重新 Add、修全库时区、调用全局时间重写或重算向量。

全量 questions 与 candidates 都要恰好覆盖相同的 861 个唯一 qid；缺题、重复题直接失败，不使用 `if hit is None: continue` 静默跳过。另检查候选与 source 的 user_id 所有权。

**这是全量题集上的检索后处理受控实验，不是重新入库与在线检索的端到端测试。** 旧全量的 74.91% 使用了混合答题模型，只作历史记录，不作为本次净提升的分母或直接基线。所有新分数使用同一次运行中的对照。

## 5. 四个实验组：明确各自回答的问题

建议四组都覆盖全部 861 题，并通过第 8 节的相同请求共享机制减少调用成本。

| 组 | 定义 | 对照目的 |
|---|---|---|
| C0 `raw_v1` | 原文证据 V1，时间注释关闭 | 实际收益必须优于的无注释对照 |
| C1 `a0_frozen` | 本次核查的 A0 完整冻结实现 | 复现已有注释方案，不能悄悄修它再称旧 A0 |
| C2 `spec_format` | 复用 C1 的 parser、路由、mention、顺序；仅将正文改为原规格字段格式 | 测“显式格式调整”是否比已有 precision 有帮助 |
| C3 `spec_safe_context` | C2 的规范字段 + 第 6 节确定性修复 + 原句关联 + 全题去重 | 推荐修订方案的整体效果 |

主要比较预先固定为 **C3−C0**；机制比较为 C2−C1、C3−C2。不得跑完后挑最高分组当唯一主结果。

C2 正文增加字段可能触及相同的 8,000 字符预算。离线比较其 resolved mention 集合与 C1：若预算回退改变了集合，单列这些题，并将总体结果称为“格式及预算影响”，不能声称是纯字段名作用。可在集合完全相同的子集报告额外的格式诊断，但主分母仍为全部 861。

全部组共享：原文快照、B0 候选、V1 原始 evidence 顺序、top_k、窗口、摘要策略、答题/判分模板、模型、温度。C3 仍不做新检索、关键词重排或强制 top-3 注释；新增收益只能称为修订方案整体收益，不能全部归因于 precision。

四组必须独立从同一份**未注释 V1 rows 的 fresh copy** 构造；不能把 C1 已注释输出再喂给 C2/C3。C3“基于 C2”仅指字段契约。各组模块用独立进程或显式依赖注入隔离，不能让 `server.temporal_evidence` 的 import 缓存或并发切换全局环境变量把旧 A0 偷换成 C3。C1 同时冻结 wrapper 与 parser/renderer。离线必须断言 C0 重放旧 V1、C1 重放现有 A0 的完整公共 rows 和最终 prompt 一致；已有 300 题保存记录可逐题核对，其余题先建立并锁定独立的旧代码重放参考。

## 6. C3 必须实现的修改

### 6.1 标注结构与原句关联

生产函数继续只接收 query、user_id 与真实证据。trace 中保存：

```json
{"schema_version":"temporal-annotation-v2","source_id":"src:...","span":[35,45],"raw_phrase":"next month","source_sentence":"We're thinking about going camping next month","sentence_span":[0,45],"observed_at":"2023-05-25T05:14:00+00:00","anchor_date":"2023-05-25","precision":"month","value":"2023-06","rule":"next_month","status":"resolved","skip_reason":null,"rendered":true,"render_skip_reason":null}
```

跨度使用原始 content 的 **0 起始、右开、Python Unicode 字符索引**，不是 UTF-8 字节偏移；必须验证 `content[start:end] == raw_phrase`，sentence_span 同理。中文、emoji、换行也要验证。不能先 strip/规范化文本，再把偏移当原始位置。

正文保留原 evidence 全文，在尾部追加条目，并带精确 source_sentence：

```text
Calendar annotations derived from the original turns:
- source_id=src:...; span=[35,45); source_sentence="We're thinking about going camping next month"; raw_phrase="next month"; observed_at=2023-05-25T05:14:00+00:00; anchor_date=2023-05-25; precision=month; value=2023-06; rule=next_month; status=resolved
```

引号、换行、反斜线用确定性 JSON 字符串转义。不能让原文分号等字符变成结构字段。机器检验读取结构化 trace，不从展示文本反解析。首版 `max_source_sentence_chars=400`，写入 experiment.json；句子超过阈值或无法准确分句时不渲染该条并记录原因，不截掉否定/条件词后生成似乎已发生的事件。句子只有“Next month”而事件在别处时，不凭空补事件名；这种跨句归属问题仍需后续单独解决。

`resolved` 仅表示时间短语可换算；`It was not yesterday but last month` 中的 yesterday 虽能算日历值，不等于事件发生在昨天。用完整原句保留这种区别；首版不新增未经验证的 factuality/人物指代模型。

### 6.2 完整短语、屏蔽与状态

当前实现的已证实缺陷及修复要求如下。这些边界缺陷已用无网络合成输入复现，尚未据此证明它们贡献了多少 LoCoMo 实际失分：

| 输入/情境 | 当前问题 | C3 要求 |
|---|---|---|
| `two days before yesterday` | 只解析内部 yesterday | 识别整段复合时间；未支持则整段 skipped，不局部匹配 |
| `three days after tomorrow` | 只解析内部 tomorrow | 同上 |
| `twenty one days ago` | 只解析 one days ago | 完整数词短语不支持则整段跳过；首版数字范围明确为 0–99，英文 one–ten；小数、带符号和超范围量也整体跳过 |
| `early next month, after yesterday's meeting` | 模糊屏蔽跨越到后面的 yesterday | 仅屏蔽限定词绑定的时间短语，不以“向后 60 字符找首个规则”推测范围 |
| 长于 200 字符的引号内容 | 引语内 yesterday 漏出 | 引语扫描不设导致漏防护的固定长度上限，兼顾转义与 We're 这类撇号 |
| 同 source_id/span 出现在多个窗口 | 每个 row 重新去重，重复标注 | 全题去重，稳定保留首次出现的位置 |
| 同句绝对日期 + last month | ambiguous 被写成 precision=day | 保留短语本身的 month 精度，value=null；原因不能随意升级为已证实冲突 |
| ambiguous 的 day before yesterday | 又输出内部 yesterday | 所有状态都占据完整跨度，避免双重记录 |
| `What year ... month-long workshop?` | 从全问句找 month，误判请求单位 | 从主问开头解析单位；该单位不得改变原句 precision/value |

同句有绝对日期只说明共现，不必然是矛盾。首版可保守跳过整句，原因记 `absolute_date_cooccurrence`；有来源证据能证明同一事件的日期不一致时，才称 conflict。不要为提高覆盖率在本轮引入复杂事件关系解析。

统一解析状态为 `resolved/ambiguous/skipped`；只有 resolved 才有资格进正文。不支持、缺时间、无时区、引语与共现等原因进入 skip_reason。**解析与渲染状态分开**：已经 resolved 但因预算、去重或原句过长未显示时，保留 status/value，另记 `rendered=false` 与 `render_skip_reason`。source 的用户隔离必须保留；source 开关关闭时，renderer 不能绕过旧回退路径。

### 6.3 精度纪律

原句的 precision/value 永远是事实层，不受问题要求覆盖。只允许从有支持的精度向更粗单位投影：day→month/year，month→year；不能 year→month 或 month→day。When 保留证据的实际精度。

若问题要日而证据只有月，不补成月初；若问题要月而只有年份，不凭 observed_at 的月份填答案。缺失信息保持不足。回答模板本轮不变；这条规则用证据契约和合成验证检查，不能通过更改 judge 来制造收益。

### 6.4 去重、预算与幂等

全题按 `(source_id, span)` 去重，不按 value 去重：两个事件同一天仍是两条来源。顺序固定为 evidence 原顺序、source_ids 顺序、原句字符位置。保留原文所有重复窗口，只去重新增注释。

C1/C2 保留现有 `max_mentions=8` 与规则优先级，以便作为冻结对照。C3 完整识别该 turn 的候选短语，按原句 span 排序，不再先按规则顺序截 8 个；随后全题去重、应用原句长度限制和 8,000 字符预算。这个差异属于 C3 的修订集合，写入配置和 trace，不冒充纯格式变化。

去重后再计算新增 8,000 字符预算，超限整题回退 C0 原 evidence；不能删原文或只保留“看起来最有用”的日期。共享 renderer 执行预算规则，评测不能专门按题回退以优待实验组。检查实际 `format_memories` 结果是否截断了原本可见内容；发生则该候选版本不能通过离线门槛。

幂等不能只依赖 `ANNOTATION_HEADER in content`，因为原文也可能出现同样标题。以内部版本/渲染来源元数据或可验证结构边界识别自己追加的块；重复执行结果完全相同。

## 7. 新增测试：17 个普通例子不足以覆盖这些边界

保留原规格 17 例，增加第 6 节所有反例，并至少覆盖：

- 多锚点：2023-05-08 的 next month→2023-06；2023-06-25 的 next month→2023-07。原规格两个锚点同月，结果相同，检测不出错误共享。
- offset 边界：按输入已编码的当地日期计算，不转成执行机器时区。上游原 session→Add timestamp→observed_at 的日期一致性单独审计，不静默全库加 8 小时。
- 句中否定、条件和计划在 source_sentence 中完整保留；resolved 不生成“事件已发生”的附加文字。
- 日→月/年的合法投影与年→月、月→日的禁止升精度；不自动改动 canonical value。
- 复合数字、复合相对时间、模糊限定、超长引语、撇号、绝对日期共现、Unicode span。
- 跨窗口重复、同日不同来源、重复 renderer、原文含注释标题、预算回退、不同用户的 source_id。
- 四组各 861 行、缺候选失败、重复 qid 失败、真实生产请求不含 gold/category/qid。

测试不能把真实题号、人物姓名或金标日期写成解析规则。合成例子用于正确性，真实题用于冻结后的效果验证。

## 8. 评测噪声控制：同请求共享一次采样

采用**同一运行内按完整请求去重的配对消融**：

1. 完整答题请求完全相同，则四组共享一次新生成答案。不能以 qid、memories hash 或旧答案正确性作为唯一缓存键。
2. 同一问题、gold、生成答案等构成的完整 judge 请求完全相同，则共享一次新判定。不能只按答案字符串缓存，因为不同问题的相同答案可能正确性不同。
3. 缓存键至少包含 run/replicate 标识、端点、模型、全部请求参数与完整 messages；不存凭据。并发使用 singleflight，避免同时漏缓存而重复调用。
4. 不同请求在题内按固定随机种子打乱组顺序，交错运行；不要永远先 C0 再 C3。并发先保持 4。
5. 每次独立 replicate 重新建立缓存；只在该 replicate 内共享，不复用历史正确答案/历史 judge。记录 logical_evaluations、unique_requests、cache_hits、score_origin 和共享来源。

此设计让未改动输入不会因重复抽样制造假分差，但**不能估计独立重跑方差**，也不能称为四组各自独立生成。它仍覆盖全量 861 题。未改动题的持平是受控输入共享的结果，需要如实标注。

若四组的差异只落在当前路由的 128 个候选题中，答题请求的理论上限为 `861 + 3×128 = 1,245` 个唯一请求（可能更少），而非 3,444 次；另有 judge 调用与失败重试。以离线计算的实际请求数为准，不承诺费用或耗时。

答题与 judge 继续使用同一配置下的 `deepseek-v4.1-flash`、`https://kspmas.ksyun.com/v1`、temperature=0；从现有私密配置读取 key，不写入文档、日志或 provenance。保存请求时间、返回 model/request_id/system_fingerprint（服务提供时）；未提供版本信息就标 unknown。

冻结 `bench/evaluators/aml_score.py` 中真正的回答与评分模板和完整请求，不用后端 prompts.yaml 代替。同答案翻判样例进入辅助审计，不人工替换正式分数。若要复测 judge 或模型稳定性，作为预先固定的独立 replicate/sidecar，不只重判输掉的题。

## 9. 对现有 harness 的必要改造

不要直接把现有 fresh-paired 改一个路径就宣称全量完成：

- `fresh_mode.build_arms` 仍引用固定 FROZEN 路径；新 runner 必须接受显式 manifest/candidates/code snapshots/source snapshot，不能残留 300 题假设。
- `run_temporal_ablation.py` 的 changed 模式仍未实现；不要调用它后误以为已测。新执行入口见第 10 节。
- preflight 不能继续要求“当前 source 模块等于最早未改动模块 hash”；应按各实验组独立冻结版本核验。
- 当前 resume 按 qid 载入 partial 并重写 provenance，没有验证配置一致。新配置 fingerprint 应覆盖题单、候选、源库逻辑内容、代码、模板、端点、模型、全部请求参数、随机种子和缓存协议；不匹配则拒绝续跑，保留旧文件。
- 单条结果用 `(run, arm, qid, request_hash)` 关联。生成成功后持久化答案，再进行 judge；judge 失败只重试同一答案的 judge，不重新生成一份答案掩盖失败。
- 产出完整结构化 trace：span、source_sentence、anchor、precision/value、rule/status/reason、row 位置、去重/预算原因；不能只存拼好的注释块。
- 所有 861 个题目必须有已完成或显式未完成状态。解析失败不能静默当错题、丢分母或跳过；有未完成题时报告 incomplete。

可以复用当前只读 store、`format_memories`、`answer_prompt`、`judge_prompt` 与标签解析器；请求客户端需保存实际 payload/非敏感返回元信息。题库和金标仅存在于评测层。

## 10. 执行顺序与命令契约

**以下入口是待实现的交付要求，当前不存在。** 在新输出目录下编写 `tools/run_annotation_full.py`；各模式实现后按顺序运行，不要覆盖既有结果。

```powershell
$py = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
$runRoot = 'F:\codex数据库memory\locomo_temporal_annotation_full_20261023'
$env:PYTHONUTF8 = '1'
$env:NO_PROXY = '127.0.0.1,localhost,::1'

# prepare：复制并冻结第4节输入/代码/模板，检查861唯一题及来源，生成experiment.json。
& $py -B "$runRoot\tools\run_annotation_full.py" --mode prepare --output $runRoot

# offline：构造四组全部861题请求；校验精度/跨度/来源/预算/未触发题保护。
& $py -B "$runRoot\tools\run_annotation_full.py" --mode offline --config "$runRoot\experiment.json"

# evaluate：只在offline通过后启动；同run同请求共享采样，四组全部覆盖。
& $py -B "$runRoot\tools\run_annotation_full.py" --mode evaluate --config "$runRoot\experiment.json" --replicate 1 --concurrency 4

# report：不改分数；并列四组结果、分类得失、变更范围、实际调用数与置信区间。
& $py -B "$runRoot\tools\run_annotation_full.py" --mode report --config "$runRoot\experiment.json" --replicate 1
```

每一步检查 exit code 和验收报告，再运行下一步。`prepare` 已有文件时拒绝静默覆盖；新 parser/格式改动使用新的版本与输出目录。先保存 experiment.json，再开始任何远端调用；不存在“测试中顺手调一下参数”。

experiment.json 至少包含四组定义、输入路径/hash、特征版本、完整非敏感模型参数、缓存协议、seed、预定比较、分母与保护要求。源库只读，服务保持原状态，评测通过前不启用生产开关。

## 11. 验收与结果解释

### 硬门槛

- 四组题目集合完全相同：861；分类 167/77/581/36。
- 原始 evidence 内容、顺序和来源不因格式实验被删改；新增注释均可溯源，精度不升级；跨用户泄漏为零。
- 未触发的 692 道非时间题完整答题请求一致。两道已知受影响单跳不得按标签绕过；分别报告 C3 相对 C0 的答案与判定。
- 完整请求去重缓存、断点续跑 fingerprint 和异常状态审计通过。
- 答题与判分模板、原文快照、B0 候选在整轮期间保持不变。

### 分数门槛

主结果为当次 **C3 相对 C0**：时间正确数增加，其他三类正确数分别不下降；不能以总分掩盖任意一类回退。建议工程目标是时间净增至少 5 / 167，约 +2.99 pp；这是目标，不是效果预测，也不是统计显著性的替代物。

若 C2 与 C1 持平，说明仅规范字段形式没有观察到收益；若 C3 提升，也应归因于整套修订，不能改写为“precision 新字段带来提升”。若 C3 仅优于 C1、却低于 C0，不建议启用。

每组报告各类正确数、分母、百分比和总分（总正确数/861，不能平均四类百分比）；对预定比较报告 wrong→right、right→wrong、净值、配对 McNemar 与差值区间。按对话聚类 bootstrap 可作辅助，说明仅 10 段对话、区间不稳，不能把题目完全独立当成无条件事实。

第一轮全量完成后即可给出结果；不要为凑过门槛反复选择最好的一次。若净差很小或区间跨零，结论为“初步/未证明稳定收益”。需要更强结论时，在规则冻结后预先固定独立复测，重建采样缓存；可只复测 167 时间题及所有被改变的非时间题，但必须明确它不是第二轮完整 861 题。

## 12. 最终交付

- 修订实现与测试结果、各实验组不可变代码快照。
- `experiment.json`、`preflight.json`、全量 `input_diff.json`、结构化 `temporal_trace.jsonl`。
- 四组完整证据、答案、judge 原文、请求 hash、缓存来源、异常与实际调用数。
- `REPORT.md`：四组分类表，主比较 C3−C0，格式比较 C2−C1，修订比较 C3−C2，得失题和局限。
- `manual_audit.md`：严格粒度争议、金标/原文冲突、相同答案的判分不一致；不覆盖 canonical 分数。

本次建议的核心是：**把时间值、精度与对应原句一起表达，先修复可证明的解析/溯源缺陷，再用能控制同输入噪声的全量对照判断效果。** 不把字段顺序、空格或换名本身视为已经验证的提分方法。
