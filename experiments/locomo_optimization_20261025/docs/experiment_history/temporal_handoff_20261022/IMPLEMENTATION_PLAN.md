# TiMEM 时间类优化实施交接：保留其他类别，增加查询期日期注释

编写日期：2026-10-23（实验与目录沿用 20261022）。状态：**设计与离线审计已完成，方案尚未实现、尚未跑新模型分数。** 本轮只生成交接材料，没有修改后端、记忆库或运行中的服务。

## 1. 给接手 Agent 的任务

先实现 **A0：保守日期主问路由 + 原句时间锚点 + 确定性日历注释**。以已经完成的 source-evidence 实验版 V1 为完整回退；保留它在多跳、单跳和推理上的证据处理路径。不要把整体回退到旧服务 B0 当作“保持其他类”。先验证未改动题目的输入，再进行成对评测。A0 通过后，才单独考虑星期解析、事件检索和时序比较，分开报告每一步的收益与回退。

所有实现只接收用户查询、用户身份和真实记忆证据。**不得向生产路由或解析器传入 category、gold、question_id、官方证据标注或这份错题清单。** 文中的题号、金标和类别仅用于离线诊断与验收。

主要依据是：时间类 21 道回退题中，15 道已经召回正确事件和时间戳，主要问题是答案仍用相对时间表达。先补足证据中的日历含义，比同时重写检索、摘要和回答提示更容易控制影响范围。它仍是待验证的优化假设，不能预先承诺追回 15 题。

## 2. 当前基线与保护对象

| 类别 | 题数 | B0：修改前服务 | V1：原文证据实验版 | V1 相对 B0 |
|---|---:|---:|---:|---:|
| 时间，category=2 | 100 | 57 / 100，57.00% | 49 / 100，49.00% | -8.00 pp |
| 多跳，category=1 | 77 | 50 / 77，64.94% | 54 / 77，70.13% | +5.19 pp |
| 单跳，category=4 | 98 | 77 / 98，78.57% | 87 / 98，88.78% | +10.20 pp |
| 推理，category=3 | 25 | 18 / 25，72.00% | 19 / 25，76.00% | +4.00 pp |
| 总计 | 300 | 202 / 300，67.33% | 209 / 300，69.67% | +2.33 pp |

本任务要保护的是 **V1 的其余三类共 160 / 200**，并使时间类优于 V1，争取超过 B0 的 57 / 100。

原实验是 **共享同一次 B0 检索候选的检索后处理消融**，不是两次独立端到端重建。答题和判分均为 `deepseek-v4.1-flash`，temperature=0；使用原有回答/评分提示。300 题按固定清单选取，覆盖 10 段对话，包含全部 77 道多跳题。它是开发 smoke，不能直接代表 861 题全量，也不能与旧混合模型的 74.91% 直接相减。

V1 总分虽提高 7 题，但 McNemar 双侧 p=0.5052，按题 bootstrap 的净提升 95% 区间为 [-3.67, 8.00] pp；尚无充分证据认定稳定提升。后续应报告实际正确题数、得失题和模型波动，不只报告总百分比。

## 3. 文件位置与真实部署状态

| 用途 | 路径 |
|---|---|
| 后端 | `F:\worktable\research\timem-aml` |
| Benchmark | `F:\worktable\research\bench` |
| 冻结实验目录，以下简称 `FROZEN` | `F:\codex数据库memory\locomo_smoke300_20261022` |
| 本交接目录 | `F:\codex数据库memory\temporal_handoff_20261022` |
| 建议新实验目录，以下简称 `OUT` | `F:\codex数据库memory\locomo_temporal_v2_20261023` |
| 原文索引 | `F:\worktable\research\timem-aml\data\aml_sources.sqlite` |
| Python | `C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe` |

已经存在于后端磁盘上的文件是 `server/source_evidence.py` 和原文 SQLite 索引。**Add/Search API 接线仅保存在 `FROZEN/app.staged.py`，尚未替换实际 `server/app.py`；原实验通过脚本直接调用证据模块。** 不要误认为线上已经运行 V1，也不要直接运行旧 `install_api.py`。

接手前阅读以下文件；保持 `FROZEN` 不变，新输出全部写入 `OUT`：

- [实验报告](F:/codex数据库memory/locomo_smoke300_20261022/REPORT.md)、[统计与来源](F:/codex数据库memory/locomo_smoke300_20261022/summary.json)、[问题清单](F:/codex数据库memory/locomo_smoke300_20261022/manifest.json)。
- `FROZEN/baseline/aml_search.json`：B0 候选；`FROZEN/variant/aml_search.json`：V1 证据；两组 `aml_score.json`：完整答案与判分。
- [冻结证据模块](F:/codex数据库memory/locomo_smoke300_20261022/source_evidence.py)、[原实验脚本](F:/codex数据库memory/locomo_smoke300_20261022/run_variant.py)。
- [当前证据模块](F:/worktable/research/timem-aml/server/source_evidence.py)、[评分代码](F:/worktable/research/bench/evaluators/aml_score.py)、[运行器](F:/worktable/research/bench/runners/aml_run.py)。
- [现有路由审计](F:/codex数据库memory/temporal_handoff_20261022/route_audit.json)、[候选路由审计](F:/codex数据库memory/temporal_handoff_20261022/candidate_gate_audit.json)。这些诊断文件含标签/答案，禁止成为生产代码输入。

本地当前没有可依赖的 Git 工作树信息。若接手时仍如此，先做文件快照与 SHA-256 清单，不要假设 `git diff` 能恢复修改。

## 4. 已确认的问题与尚未确认的假设

### 4.1 时间类回退分布

100 道时间题：36 道两版都对，30 道两版都错，13 道 B0 错/V1 对，21 道 B0 对/V1 错。

| 21 道回退的可观察原因 | 数量 | 首选处理 |
|---|---:|---|
| 正确事件和时间戳已召回，输出仍为相对时间 | 15 | 查询期日历注释；其中 1 题已有锚点但严格 judge 不做换算 |
| 模糊时间短语改写后被严格 judge 拒绝 | 1 | 保留模糊程度；另列评测口径审计 |
| 关键原文没有进入前 20 个证据窗口 | 3 | 后续单独改事件检索/上下文窗口 |
| 证据已存在，但选错事件或比较顺序 | 2 | 后续单独做事件约束与时序推理 |

这不是“49% 的事实召回率”。判分还受生成形式、时间粒度和事件选择影响。

### 4.2 当前路由不能隔离任务类别

现有 `_TEMPORAL` 搜索 `when/before/after/first/...`。结果是：时间类仅命中 82 / 100，其他三类却命中 13 / 200。命中后会直接去掉摘要，仅保留最多 20 个原文窗口；未命中则原文加前 4 条摘要。

误触发例子包括 `first place`、`when he was younger`、`What ... after ...`。但非时间类别也有真正的日期/时长主问，例如多跳中的 `When did Melanie go on a hike after the roadtrip?`。因此，不能用“带 when 就是时间类”来承诺保护其他类别。

这份交接的候选主问规则在冻结 300 题上得到 **75 道时间类候选、0 道其他类候选**。这只是字符串路由离线结果，还没有检查证据资格，也没有新跑分。实际渲染覆盖率会更低。所有 200 道保护题仍必须逐题验证。

### 4.3 两个必须单列的来源问题

1. 实际 AML loader 在 `aml_loaders.py::_locomo_messages` 给同一 session 的所有 turn 同一个时间。日期解析得到 naive datetime，`_iso_to_ms` 通过宿主时区转 epoch，而证据模块 `_iso` 转为 UTC，存在跨日风险。另一份 loader 的“逐 turn 增加一分钟”不是本次路径。**本轮冻结现存 `observed_at`，不改 loader、不硬加 8 小时、不重导库。** 对来源日期有冲突的条目禁用注释并记录。
2. `FROZEN/backfill_sources.py` 从当前 loader 重建 Add，再校验历史响应成功及 user/session；它没有逐字核对历史原始 messages/timestamp。因此，当前代码不能证明重建内容与当时 Add 请求逐字相同。可以确认现存索引内容固定、来源字段范围受限；“历史请求逐字复原”应保留为待核查项。不要在本轮静默重建索引来掩盖这一点。

## 5. A0 的改动范围与接口

### 5.1 必须保持的行为

- 保留 V1 的 FTS 查询词、OR 召回、排序、±2 相邻 turn、开头上下文、20 个窗口、80,000 字符限制、top_k 和摘要拼接策略。
- 保留所有证据的 ID、顺序、score、created_at、source_ids、角色、原文。只有获准的查询可以在原始证据内容后追加注释。
- A0 不新增或改变 `stamp_temporal` 的现有调用，不运行批量重盖时间的脚本；现有 Add 已有该调用，不要为本轮删掉它。不重写 L1/L2，不重算向量，不改 embedding、搜索扩展、回答提示、judge 提示、模型或温度。
- 不新增 LLM 路由/抽取调用，不根据旧答案正确与否选择路径，不把金标内容补入证据。
- 新功能默认关闭。关闭、未命中、无法解析、缺源或发生可恢复错误时，返回**完整 V1 结果**，不是 B0 结果。

### 5.2 建议文件清单

以下是待实现接口，并非已经存在的代码。

| 文件 | 具体工作 |
|---|---|
| `server/temporal_evidence.py`，新增 | 纯函数路由、相对时间解析、注释渲染；不访问 benchmark |
| `server/source_evidence.py` | 保留/抽出 `_ground_evidence_v1`；新增按 user_id + source_ids 只读取 turn 的方法；在 V1 完整结果上调用渲染器 |
| `tests/test_temporal_evidence.py`，新增 | 日历边界、精度、拒绝路径、原文保持、幂等、跨用户隔离 |
| `OUT/tools/run_temporal_ablation.py`，新增 | 冻结输入重放、字节/hash 对照、断点续跑、成对答题/评分 |
| `OUT/tools/report_temporal_ablation.py`，新增 | 分类统计、得失题、缓存标识、异常与耗时报告 |
| 新版 API staged 补丁，保存到 `OUT` | 验证后再准备接线；不直接覆盖旧快照或运行中的服务 |

建议开关：`TIMEM_TEMPORAL_RENDERER=off|calendar_v1`，默认 `off`。现有 `TIMEM_SOURCE_EVIDENCE=0` 的 B0 回退语义保持原样。

接口建议：

```python
def plan_date_query(query: str) -> DateQueryPlan | None: ...

def read_source_turns(store, user_id: str, source_ids: list[str]) -> dict[str, SourceTurn]: ...

def parse_time_mentions(turn: SourceTurn) -> tuple[TimeMention, ...]: ...

def render_date_evidence(plan, legacy_rows, turns) -> RenderResult: ...

# 保留旧开关优先级，不让 renderer 绕过原有 B0 回退。
if not source_evidence_enabled():
    return original_source_disabled_behavior(rows, top_k)
# legacy_rows 先由冻结 V1 行为生成，所有拒绝路径原样返回。
legacy_rows = _ground_evidence_v1(user_id, query, rows, top_k, store=store)
if not temporal_renderer_enabled():
    return legacy_rows
plan = plan_date_query(query)
if plan is None:
    return legacy_rows
result = render_date_evidence(plan, legacy_rows, owned_source_turns)
return result.rows if result.changed else legacy_rows
```

`DateQueryPlan` 至少包含 `intent=DATE_VALUE`、请求精度（或 unspecified）、规则版本。`TimeMention` 至少包含 `source_id`、原句字符跨度、raw_phrase、完整 observed_at、anchor_date、precision、normalized_value、rule_id、限定词、解析状态。用 `resolved/ambiguous/unsupported` 表示状态；不要用未经校准的 0.95 等数字冒充置信度。

`read_source_turns` 用参数化 SQL，强制 `WHERE user_id=? AND id IN (...)`，按需分批，返回与 source_ids 对应的原文记录。评测读取冻结 SQLite 时使用只读连接，避免构造 store 时执行初始化写入。不要从拼接后的显示文本猜测 source_id、角色和时间边界；原文可能多行或包含类似日期标签。读取不到、所属用户不符或内容对不上时，保留原证据并记录原因。

公开 API 目前只返回 id/content/score/created_at；注释必须写入 content 才能到达回答模型。source_ids 与审计字段保留在内部，不必扩展公开协议。接线位置在 staged Search 的所有权过滤之后、公开序列化之前；继续保留 `filter_owned` 的线程调用，避免恢复此前的事件循环阻塞问题。

## 6. A0 路由规则

### 6.1 第一层：主问形态

只接受询问日期值的整句主问，例如：

- `When did/is/was/has/will ...?`
- `What/Which date/day/month/year/week ...?`

以下是已离线审计的**候选筛选器**，实现可从它开始；它不等于完整自然语言语义判断：

```python
PREFIX = r"^(?:when\s+(?:did|does|do|is|are|was|were|has|have|will|can)\b|(?:what|which)\s+(?:date|day|month|year|week)\b)"
EXCLUDE = r"\b(first|last|earliest|latest|recently|before|after|since|until|earlier|later|second|third|fourth|fifth)\b"

# re.I；输入先 strip；必须 PREFIX 命中且 EXCLUDE 不命中。
```

EXCLUDE 在问题上运行；它不禁止从证据中解析 `last month`。这是保守的首版范围，可能主动放弃可回答的日期问题。

完整 planner 还应拒绝多个独立事件的日期计算、`how long`/时长、事件相对 before/after、首次/末次、排序比较、`day of the week` 等非日期值需求。多个参与者参加同一个事件不等于多个事件，不要简单用出现 `and` 就拒绝所有问题。识别不确定时回退。

新 gate 与旧 `_TEMPORAL` 独立判断。否则 `Which month/year/day/week` 等旧规则漏中的日期问题永远进不来。**不要修改旧 regex 的行为作为副作用**：拒绝新 gate 的题即使旧 regex 曾误命中，也要返回它原来的 V1 raw-only 结果。

### 6.2 第二层：证据资格

- 至少有一条属于当前用户、具有可解析 observed_at 的原始 turn，且含 A0 支持的时间短语。
- 只能使用该短语所属 turn 的时间，不得用 chunk 首日、相邻 turn 日期、窗口外层 created_at 或系统今天。
- 不把引语、假设、故事中的相对时间强行锚定到说话日期。首版对明确引号内的短语、历史叙述另设时间原点、嵌套转述、无法区分所属事件的表达跳过，并输出跳过原因。
- 不根据查询里的日期去替换缺失的发言时间；问题提供的约束与证据锚点分开。
- 不重选目标事件、不据此删掉其他证据。A0 仅解释原句时间短语，保留原句的计划、愿望、否定等语气，不把“计划下月去”变成“下月已经去过”。

没有任何安全可注释的短语时，整个结果与 V1 保持字节相同。

## 7. 日期解析与渲染规格

### 7.1 A0 支持范围

| 原句短语 | 结果精度 | 例子 | 处理要求 |
|---|---|---|---|
| today / yesterday / tomorrow | day | 2024-03-01 的 yesterday → 2024-02-29 | 日历加减，支持跨月、跨年、闰年 |
| day before yesterday / day after tomorrow | day | 2022-07-09 的后天 → 2022-07-11 | 长短语优先匹配，避免再次匹配 tomorrow |
| N days ago | day | 2023-07-12 的 two days ago → 2023-07-10 | 首版支持数字与明确英文 one–ten；其他量词回退 |
| last / this / next month | month | 2023-05-25 的 next month → 2023-06 | 只输出 YYYY-MM，不补 01 日 |
| last / this / next year | year | 2023-06-26 的 last year → 2022 | 只输出 YYYY，不补月日 |

先标记引语、模糊限定和不支持表达的完整禁止跨度，再在其外按最长跨度匹配；每个 source_id + 字符跨度只生成一次。例如 `early next month` 必须整体屏蔽，不能偷偷匹配内部的 `next month`；`about two days ago` 也不能输出伪精确的某一天。已有绝对日期不重新解释。若原句明确同时给出绝对日期和相对日期且不一致，标记冲突、跳过这处注释，不私自选择“正确”的日期。

时间运算使用冻结 observed_at 中已编码的日期/偏移；不依赖运行机器当前时区。缺失、非法或来源日期存在未解决冲突时跳过。现存 UTC 日期是否保留了数据集原本的民用日期属于第 4.3 节的独立诊断，不能声称 A0 已修复。

### 7.2 A0 明确不解析的表达

`last/next Sunday` 等星期；`last week/two weeks ago/this weekend`；季节；`a few years ago`；`early/late next month` 等带模糊区间限定的表达；持续时长；`in N days`；“另一件事之后三天”。`I finished it in two days` 表示耗时，不是发言日加两天，因此 A0 暂时整体不支持 `in N days`。原文保持原样，不人为补精度或给不确定语义选唯一日期。

原因包括：星期与周末存在习惯性歧义；现有回答提示要求 week 表达保持相对，judge 又不进行相对/绝对换算。A0 先处理口径清晰的日期/月/年，避免把评分协议问题混入解析器。

后续 A1 可单独增加 weekday 规则：必须写明日历约定、同一天的处理、已排除的歧义和独立用例，再单独消融。不要直接调用 `time_anchor.py::_resolve_relative` 对整段文本替换；该函数还会扩展 week/weekend，且没有所需的原句来源与精度约束。

### 7.3 注释格式

保留原记录 content 全文，在记录尾部追加独立块；不重写原句，不把注释拼成已确认的事件答案。例如：

```text
Original conversation evidence:
[2023-05-25T12:00:00+00:00] assistant: We're thinking about going camping next month.

Calendar annotations derived from the original turns:
- source_id=src:...; raw_phrase="next month";
  observed_at=2023-05-25T12:00:00+00:00;
  calendar_value=2023-06; precision=month; rule=next_month.
```

每条注释要能定位到原句 source_id 与短语；多个相同短语来自不同 turn 时不能串用锚点。跨窗口重复的同一 turn 允许各自显示，但同一记录内部不得重复，重复执行 renderer 不得叠加第二份注释。

在生产与评测共享的 renderer 中增加明确预算，例如每题新增内容最多 8,000 字符；超过预算则整题回退 V1，并记录 `annotation_budget_exceeded`。离线 harness 还要确认 `format_memories` 没有因追加注释而截掉原来可见的任何证据记录；出现截断则本候选版本未通过预算验收。修正共享预算后重新整批离线检查，harness 不得自行替换该题为 V1，否则评测行为与实际 renderer 不同。不要为了挤入注释删摘要、缩原文或改变原检索预算。

## 8. 如何证明没有改变其他类别

只看三类分数持平不够，可能发生得失题抵消。先做输入级保护，再做分数验证。

对冻结 300 题，离线执行 V1 和 V2，记录：

1. 有序公开 rows 的 canonical JSON hash：字段 id/content/score/created_at，保留字段值和数组顺序；对象键统一排序，不做空白/Unicode 语义归一化。
2. 内部 source_ids、level 等已存在字段的值与顺序检查；A0 不应改变它们。
3. `format_memories` 结果的 UTF-8 SHA-256。
4. 完整 answer prompt 和请求参数的 SHA-256，含模型、temperature、选项与其他实际参数；排除凭据。不要只比对 memories。

**200 道非时间题上述输入必须逐题一致，任意一题变化就判 A0 隔离失败。** 先修正通用语义规则或回退条件，不允许加 qid 白名单/黑名单把差异藏起来。时间类中没有获准注释的题同样应一致。

首先用冻结代码和相同 B0 candidates 重放 V1，确认它与保存的 V1 公开 rows、format_memories、prompt 一致；不一致时定位环境、序列化或源索引差异。未解决前不得给 V2 做正式对比。也可直接从保存的 V1 rows 运行纯 renderer，作为独立的最小消融。

这只能证明**本批固定题目的输入不变**。生产语义规则无法保证未来所有标注为单跳/多跳的日期问题都不受影响。temperature=0 也不能保证远端模型逐字重现；必须把模型波动与输入变化分开报告。

## 9. 最小测试清单

测试使用合成原句/查询和真实来源结构；真实题只作为离线回归审计，不能写成“遇到这个问题返回某日期”的单元测试。

| 测试 | 预期 |
|---|---|
| 2024-03-01 yesterday；2023-03-01 yesterday | 分别 2024-02-29、2023-02-28 |
| 2023-12-31 next month / next year | 2024-01，month；2024，year |
| 2023-01-01 yesterday | 2022-12-31，day |
| 2023-07-12 two days ago | 2023-07-10，day |
| 同一批证据两句分别在 5 月 8 日、5 月 25 日说 next month | 各自锚定，不借用外层时间；source_id 可追溯 |
| `a few years ago` / last week / next Sunday | A0 不注释，原文不变 |
| `early next month` / `about two days ago` | 整体禁止跨度，不匹配内部的支持短语 |
| `I finished it in two days` | 不能误解析为发言日之后两天 |
| 明确引语里的 yesterday、缺时间、非法时间、冲突日期 | 跳过并记录原因 |
| 两个不同用户持有相似原文；输入混入另一个用户的 source_id | 不读出其他用户内容，不增加未经授权的注释 |
| `first place`、when 背景从句、How long、first travel、after the trip | 新路由拒绝，V1 输入完全不变 |
| `Which month ...?`、`What day ...?` | 可进入新主问 gate，不依赖旧 regex |
| 多事件归属不清或事件相对日期 | 回退，不生成伪精确日期 |
| renderer 连续执行两次 | 相同结果，不增加重复块 |
| feature off / top_k<=0 / 无 sources / 预算超限 | 与旧行为一致，不原地修改输入 rows |
| SOURCE_EVIDENCE 与 TEMPORAL_RENDERER 开关的四种组合 | source 关闭始终走旧 B0 回退；source 开启且 renderer 关闭为 V1 |
| 全部 300 题重放 | 200 题保护断言全部通过；所有变更都有 trace |

保存原有证据模块测试和假服务 API 测试。后续接线测试验证 Search 公开 content 确实包含注释、所有权过滤仍在、Add 幂等行为未改变。无需为本轮优化重新灌入 5,882 条消息。

## 10. 实施与评测顺序

### 第一步：冻结与预检，不调用模型

创建 OUT，记录代码/索引/manifest/提示词 hash 和非敏感模型配置；保留 FROZEN。读接口配置时只输出 model/base_url/timeout 等必要项，**不要输出 .env 全文或 API key**。

预检重要参考 hash：

| 对象 | SHA-256 |
|---|---|
| 冻结 source_evidence.py | `f9d9ca1532266d1302e28139433fd6ad2d5693693d072787375e28c10a6f4835` |
| 上轮结束时原文索引 | `cf56a1cf814eca61d5ec30b3a9722ddd4c8d6bd5a85c6868ebaab10477ad5888` |
| manifest 选择清单文件 | `b99c43721dafec8321ba1ccd95d5201a6bc853f97de1af69bbf111c6f183d92b` |
| 上轮 369 条核心记忆内容聚合 | `21ee7dbc4ea43acf320c547e102bb84df4fff2cb2b43d1dd0af4f6668097834d` |

最后一项是原审计脚本定义的聚合 hash，不是单一数据库文件 hash；按 `FROZEN/audit_state.py` 的口径核对，改为输出到 OUT，不直接运行其默认写入 FROZEN 的入口。若索引后来被合法写入，不要删数据恢复旧 hash，应快照并说明新实验基准已变化。

`preflight` 模式先通过 SQLite backup API，在 `OUT/frozen/aml_sources.sqlite` 建立一致快照；不要只复制可能存在 WAL 的活动主文件。记录源文件检查值、快照文件 hash，以及按主键排序的 source_turns/source_requests 内容 hash，随后完成 V1 重放核验。backup 可能改变文件头等物理字节，不能仅因文件 hash 不同断言内容漂移；以逻辑内容、来源和重放结果共同判断。所有后续模式与断点续跑使用同一快照，只读打开，不再读活动库。

### 第二步：实现 A0 + 离线审计

先完成新纯函数模块及测试，再接证据函数开关。复现 V1，生成 V2 证据、完整请求 hash 和 temporal trace。**保护题检查通过之前不花模型调用成本。** 新 harness 从实际旧 runner 的 `_score_endpoints`、`_score_question_row`、`format_memories`/`answer_prompt` 复用接口；不要重写另一套评分提示。

新 harness 需要实现以下模式：

| 模式 | 行为 | 能说明什么 |
|---|---|---|
| `preflight` | 一致快照、来源清单、配置和文件 hash；不调用模型 | 建立其余模式共享的冻结源库 |
| `offline` | 300 题重放与比较，只做本地处理 | 路由、原文、来源、输入保持、预算与延迟 |
| `changed` | 只对请求 hash 改变的题调用答题与 judge；其他题复用明确标识的旧 V1 记录 | 低成本开发诊断；不是独立新跑 300 题 |
| `fresh-paired` | 对全部 300 题分别用 V1、V2 新调用答题与 judge，配对交错调度 | 当次模型条件下的完整成对验证 |

`changed` 选择依据是请求 hash，不能按 category 或历史得失挑选。报告必须分别列出 `fresh_count`、`reused_count`、原记录路径/hash、`score_origin`，不得将复用后的 300 行宣称为“重新完整评测”。

断点续跑至少校验 run 配置、代码、source snapshot、manifest、prompt、model/base_url 的非敏感 hash 一致；原子写 partial 文件；仅重试失败请求。judge 解析失败保留为失败状态，不能随意记错题或跳过分母。建议最多 4 个并发任务，沿用本地既有重试规则，并记录真实调用数。

### 第三步：运行命令约定

**下列新脚本和 CLI 是交付要求，当前尚不存在；接手 Agent 实现这些入口后再执行。** 以下 PowerShell 示例不含密钥；应继续从现有配置读取模型凭据。

```powershell
$py = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
$frozenRun = 'F:\codex数据库memory\locomo_smoke300_20261022'
$temporalOut = 'F:\codex数据库memory\locomo_temporal_v2_20261023'
$activeSourceDb = 'F:\worktable\research\timem-aml\data\aml_sources.sqlite'
$frozenSourceDb = "$temporalOut\frozen\aml_sources.sqlite"
$env:PYTHONUTF8 = '1'
$env:NO_PROXY = '127.0.0.1,localhost,::1'

# preflight 创建一致快照；已有快照不得静默覆盖。
& $py -B "$temporalOut\tools\run_temporal_ablation.py" --mode preflight --frozen-run $frozenRun --source-db $activeSourceDb --output "$temporalOut\frozen"
# harness 内分别构造 V1 与 V2，不靠全局开关污染两组；以下全部读取同一个只读快照。
& $py -B "$temporalOut\tools\run_temporal_ablation.py" --mode offline --feature calendar_v1 --frozen-run $frozenRun --source-db $frozenSourceDb --output "$temporalOut\offline"
& $py -B "$temporalOut\tools\run_temporal_ablation.py" --mode changed --feature calendar_v1 --frozen-run $frozenRun --source-db $frozenSourceDb --output "$temporalOut\changed"
& $py -B "$temporalOut\tools\run_temporal_ablation.py" --mode fresh-paired --feature calendar_v1 --frozen-run $frozenRun --source-db $frozenSourceDb --output "$temporalOut\fresh"
& $py -B "$temporalOut\tools\report_temporal_ablation.py" --run "$temporalOut\fresh" --historical-run $frozenRun
```

每一步检查 exit code 与前置报告通过再运行下一步；不能把以上命令不加判断当作自动通过的批处理。`fresh-paired` 会新生成两组各 300 题的答案与判分，调用成本高于单组 smoke。若只能完成 `changed`，最终必须明确验证边界。

### 第四步：验收与报告

| 层级 | 门槛 |
|---|---|
| 输入保护，硬门槛 | 200 / 200 非时间题公开证据、内部来源、format_memories、完整答题请求一致；非获准时间题也一致 |
| 事实与来源，硬门槛 | 原文不变；没有跨用户来源；每条注释可由 source_id + raw_span + observed_at 复算；无金标/标签进入生产逻辑 |
| 开发诊断 | 明确说明相对历史 V1 的得失题；时间 >49 只表示优于 V1，恢复到 57 仅表示追平旧 B0 |
| 本批历史目标 | 时间至少 58 / 100 才称超过两组历史时间基线；建议工程目标 ≥60 / 100。其他三类至少保持 54 / 77、87 / 98、19 / 25 |
| 新成对验证 | 当次 V2 时间正确数高于当次 V1；其余三类分别不下降。另与历史 B0/V1 并列，不能只挑有利参照 |
| 稳定性 | 固定设计后使用未参与本轮规则制定的时间题；必要时做重复成对运行，报告均值、范围、得失题与置信区间 |

若时间达到 60 且其他类保持历史 160，则总分是 220 / 300 = 73.33%。**这是工程目标的算术结果，不是预测成绩。** 当前 A0 表面上可覆盖 9 个“昨日/明日/下月”回退样例，但其中任何题都可能仍因生成或语义判断失败，不能按 49+9 宣称新分数。

未变化的输入在 fresh 模式下若出现分数下降，明确记录为相同输入的输出/判分波动，不能改 category 路由或悄悄复用历史较好答案。没有满足本批分数门槛时，报告“输入隔离通过，分数保持尚未验证/未通过”。

保持回答提示和 judge 提示不变。人工确认“日期等价”“金标与原文冲突”可以附在报告中，但不能修改 canonical 分数。统计至少包含分类正确数/分母/百分比、相对 B0 与 V1 的差值、逐题得失、judge 失败、缓存比例、证据 token、处理延迟。

这 100 道时间题已用于设计，不能再称独立 holdout。扩展验证先固定清单；即使来自剩余全量题，也要说明项目此前可能评测过它们，不包装成完全未见测试集。不要根据新增验证的金标反复微调并仍称其为保留集。

## 11. A0 之后的方向，必须单独消融

| 优先级 | 方向 | 解决的问题 | 实现边界 |
|---|---|---|---|
| A1 | 保守 weekday 解析 | Last Friday / Next Sunday 等已有原文的回退 | 定义语义约定与歧义回退；不同时调整 week/weekend |
| B1 | 事件约束的原文检索 | 养龟时长、第三次比赛、加拿大出发原句遗漏 | 只对获准新意图启用；先离线量化所需原句是否进入候选，再测答案 |
| B2 | 事件时间表与比较 | 首次东京、两条蛇领养先后 | 保存 entity/event/observed_at/event_time/precision/source_id；证明两项证据的实体归属后才排序 |
| C | 入库时间边界与摘要来源链 | 跨日期 chunk 的旧摘要锚点错误 | 改摘要分块/来源字段会影响各类，必须新建实验与完整重测，不混入 A0 |

B1 的可试验方案：用原有语义摘要作为检索线索补找原句；通过事件关键词/同义词扩展与相邻问答恢复原文，限定同用户；只把找回的有来源原句作为日期依据。摘要日期与原句冲突时保留冲突记录，不把错日期重新注入。`tourney/tournament` 的词形差异、宽泛人物名/third 竞争以及 ±2 窗口截断目前是合理假设，不是已证实的单一根因。不要盲目把 top_k 从 20 扩大为 100 就宣布解决。

B2 不能用“取最早发言日期”替代“首次事件”：较晚的发言可能回忆更早的旅行。比较不精确月份、年份、区间时采用区间关系；重叠则不强行排序。指代无法可靠解析时保留 V1 回退。

这些方向与 [TiMem 论文第 3、4.6 节](https://arxiv.org/html/2601.02845v2) 关于保留细粒度事实、利用时间层级组织记忆的思路一致。本文的 A0 是针对本地失败模式提出的工程方案，不是论文已经验证的新增算法。论文使用的题集与本地 861 题不同，不引用论文分数作为验收基线。

## 12. 代表性案例与复核入口

下表仅供评测调试。证据排名指 `FROZEN/variant/aml_score.json` 的 `search.evidence` 一基序号；缺失原文通过当前 SQLite 只读核查。实现中禁止硬编码题号、名字、金标日期。

| qid | 已核查现象 | 对方案的意义 |
|---|---|---|
| conv-26#q0006 | 5 月 25 日原句 “going camping next month” 在 rank 1；V1 只答 Next month，金标 June 2023 | A0 的 month 注释直接针对这一机制 |
| conv-30#q0020 | 6 月 16 日原句昨天获得创业指导；V1 正确答 6 月 15 日；旧摘要误写 6 月 12 日 | 必须保留已经获得的原句锚点收益 |
| conv-30#q0010 | tattoo 原句 A few years ago；V1 改成 A few years before February 2023 后判错 | 模糊短语不应强行精确化；另列口径复核 |
| conv-42#q0003 | “I've had them for 3 years now” 存于 2022-01-23，source_id=`src:7d2af0e4b1f52a813eea434d90a28467`，未进入该题前 20 组；旧摘要有 3 years | A0 不解决此类真实召回遗漏 |
| conv-42#q0020 | 第三次比赛所需 2022-06-03 的 last week 原句缺失，source_id=`src:ec37c0f7f805972f8f241229532f6daa`；混入第二次比赛等上下文 | 后续做事件去混淆和召回，不能凭日期解析修复 |
| conv-47#q0018 | 2022-07-09 的 day after tomorrow evening 原句缺失，source_id=`src:acb57c859b9b10c783052d5581fb891b`；金标 7 月 11 日 | 解析器即使支持后天，也救不了未召回句子 |
| conv-47#q0035 | V1 答 Next Sunday after 2022-09-04，金标 2022-09-11；严格 judge 不做换算 | A1 与评分形式问题，不能宣称原事实错误 |
| conv-50#q0000 | 首次东京旅行的 4 月 20 日证据在 rank 1，5 月再次旅行在 rank 2；V1 选成 5 月 | 需要首次事件选择，A0 主动回退 |
| conv-48#q0051 | Seraphim：2023-06-26 说去年；Susie：2023-08-01 说两年前；证据均在，但 V1 比较反了 | 需要实体指代和比较，不靠改主问 regex 修复 |
| conv-42#q0067 | 金标 2022-11-10；当日原句说昨天去海滩；V1 答 2022-11-09，判错 | 金标与原句存在冲突，单列人工复核，主分数仍按冻结协议 |

9 个与 A0 支持短语表面吻合的回退题是：`conv-26#q0006`、`conv-26#q0048`、`conv-42#q0048`、`conv-42#q0057`、`conv-43#q0019`、`conv-47#q0034`、`conv-48#q0075`、`conv-50#q0030`、`conv-50#q0046`。它们仅用于检查原因，不能用作运行时命中列表。

## 13. 接手 Agent 的最终交付物

- 新模块、最小接线改动与测试结果；包含与修改前文件的 diff 或可恢复快照。
- `preflight.json`：输入/代码/提示/配置 hash，明确现存数据源与时间来源限制。
- `input_diff.json`：300 题逐题证据和请求 hash，200 题保护断言，以及所有获准/拒绝原因。
- `temporal_trace.jsonl`：source_id、跨度、原短语、锚点、结果、精度、规则版本、跳过/冲突原因；不包含密钥。
- 各模式独立目录下的 `aml_search.json`、`aml_score.json`、逐题来源/缓存标识；不可覆盖 FROZEN。
- `REPORT.md`：三组历史/当次对照、分类分数、净得失、边界与是否通过门槛；对不达标如实结论。
- 若验收通过，再提供可审阅的 API 接线补丁与开关回退说明。回退 A0 只需关闭 `TIMEM_TEMPORAL_RENDERER` 并按服务配置方式重新加载；不要删索引或重写核心记忆。若 V1 API 接线尚未上线，关闭 A0 只影响实验函数路径，不能说线上已完成回退。

本交接的完成标准是让另一个 Agent 可以按上述顺序实现、审计、运行并判断有效性；不是保证尚未实测的方案一定提高分数。
