> **评审版 · 2026-10-23**
>
> 原文保留，逐节插入【评审】批注；原始文件未改。原文中的指令是待审规格，不是本次执行授权。
>
> **总评：可行，但核心 precision 能力当前已实现。** 建议把“标准化字段”与“修复解析并关联原句”分组测量。只有支持范围内且锚点明确的表达才具有确定输出，不应承诺任意原句都能唯一解析。
>
> 配套执行文档：[861 题全量执行方案](F:/codex数据库memory/temporal_annotation_review_20261023/EXECUTION_PLAN.md)。当前 300 题中时间 71→70，且发现相同答案判分翻转，尚不能认定格式有效或确定有害。全量路由会改变 2 道单跳题，须真实验收，不能按类别绕过。
>
> 原文 SHA-256：`da18e4c5067c10f86d1d55ebe844fcf0da27b20aef551fe410d63fdd9a40bc21`。

# 时间证据标注规格（day / month / year 精度）

> 用途：给答题模型提供"已算好的日历值"，避免它自己做相对→绝对换算。
> 本文是可独立验证的规格：任何人（或模型）拿到输入原句，都应当能唯一确定输出。

---

## 1. 数据前提

> **【评审 R1｜保留 + 补前提】** 读取原始 turn、使用自己的 observed_at 是正确方向。但“有时区”不等于上游会话日期已经转换正确。应单独审计 session 日期 → Add timestamp → observed_at；本轮冻结源库，发现冲突则跳过，不静默全库加八小时。当前 `source_evidence.read_source_turns` 已按 user_id 读取此类数据。

标注读取的是**原始对话 turn**，不是摘要、不是 L1/L2 记忆。

| 字段 | 说明 | 示例 |
|---|---|---|
| `source_id` | 该 turn 的唯一 id | `src:9c8327c28393d9ee2837c93a7b113266` |
| `content` | 原句文本 | `We're thinking about going camping next month` |
| `observed_at` | 该 turn 的发言时间（带时区） | `2023-05-25T05:14:00+00:00` |
| `anchor_date` | 由 `observed_at` 取日期部分 | `2023-05-25` |

**锚点纪律**：只能用该 turn 自己的 `observed_at`。禁止用 chunk 首日、相邻 turn 日期、窗口外层时间或系统当前日期。

---

## 2. 标注块格式（追加，不改原文）

> **【评审 R2｜必须说明已有能力；建议加原句关联】** 当前 A0 正文已经有 `precision`、`calendar_value`、raw_phrase、observed_at、rule。这里的新变化主要是 `anchor_date/status` 和 `calendar_value→value`，尚无证据说明固定字段顺序或去掉等号两边空格会提分。把这一版本单独作为格式实验。
>
> **推荐修订**：每条注释增加原文 `span` 与精确 `source_sentence`/`sentence_span`。原 evidence 各 turn 行中没有显示 source_id，单独一串 ID 和 “next month” 很难帮助模型区分哪个事件。保留原句中的否定、条件、计划；不要改成“建议答案”。字符串采用 JSON 转义，机器验证读取结构化 trace，不从分号文本反解析。

```text
Original conversation evidence:
[2023-05-25T05:14:00+00:00] assistant: We're thinking about going camping next month

Calendar annotations derived from the original turns:
- source_id=src:9c8327c28393d9ee2837c93a7b113266; raw_phrase="next month"; observed_at=2023-05-25T05:14:00+00:00; anchor_date=2023-05-25; precision=month; value=2023-06; rule=next_month; status=resolved
```

字段顺序固定，分号分隔，值不加空格（`value=2023-06` 不是 `value = 2023-06`）。

---

## 3. 精度与值格式（核心表）

> **【评审 R3｜保留；必须补完整短语边界】** day/month/year 的值精度规则正确，当前代码已具备。数字范围应明确，建议首版数字 0–99、英文 one–ten。对超出范围的完整数词短语整段跳过，不能在 `twenty one days ago` 内只解析 `one days ago`（现有代码会这样做）。
>
> `two days before yesterday`、`three days after tomorrow` 是完整复合表达，不能只匹配内部 yesterday/tomorrow。首版整体 skipped；这比输出一个确定但错误的日期更重要。规则先识别完整跨度，再判断是否支持。

| 原句表达 | precision | value 格式 | 例子（anchor → value） |
|---|---|---|---|
| `today` | `day` | `YYYY-MM-DD` | 2023-05-25 → `2023-05-25` |
| `yesterday` | `day` | `YYYY-MM-DD` | 2023-05-25 → `2023-05-24` |
| `tomorrow` | `day` | `YYYY-MM-DD` | 2022-11-04 → `2022-11-05` |
| `day before yesterday` | `day` | `YYYY-MM-DD` | 2023-05-10 → `2023-05-08` |
| `day after tomorrow` | `day` | `YYYY-MM-DD` | 2022-07-09 → `2022-07-11` |
| `N days ago`（数字或 one–ten） | `day` | `YYYY-MM-DD` | 2023-07-12 + `two days ago` → `2023-07-10` |
| `last month` | `month` | `YYYY-MM` | 2023-10-23 → `2023-09` |
| `this month` | `month` | `YYYY-MM` | 2023-05-25 → `2023-05` |
| `next month` | `month` | `YYYY-MM` | 2023-12-31 → `2024-01` |
| `last year` | `year` | `YYYY` | 2023-05-25 → `2022` |
| `this year` | `year` | `YYYY` | 2023-05-25 → `2023` |
| `next year` | `year` | `YYYY` | 2023-12-31 → `2024` |

**铁律：value 的精度必须等于 precision。**

```text
precision=month  → value 只能是 2023-06，不能是 2023-06-01
precision=year   → value 只能是 2022，不能是 2022-06 或 2022-06-15
precision=day    → value 才是完整日期
```

---

## 4. 不标注的表达（必须原样跳过）

> **【评审 R4｜必须修正“共现即冲突”与屏蔽边界】** 同句出现绝对日期，不一定与相对日期指向同一个事件。首版保守跳过整句可以接受，但理由应是 `absolute_date_cooccurrence`，不能声称已经证明事实矛盾；后续再单独评估从句级解析。
>
> “整段跳过”必须对应原文字符跨度：`early next month, after yesterday's meeting` 只应屏蔽前面的模糊时间，不应连带吞掉后面的 yesterday。当前实现按规则顺序向后搜 60 字符会错误扩大范围。引语扫描也不能以 200 字符为上限，否则长引语里的 yesterday 会被误标。继续排除时长、星期/周末、季节和事件相对表达，避免本轮同时扩范围。

| 类型 | 例子 | 处理 |
|---|---|---|
| 星期 | `last Friday`、`next Sunday` | 不标注（歧义：含"当天"与否不确定） |
| 周/周末 | `last week`、`this weekend`、`two weeks ago` | 不标注（官方提示词要求保留相对） |
| 季节 | `last summer` | 不标注 |
| 模糊量词 | `a few years ago`、`several months ago` | 不标注 |
| 模糊限定 | `early next month`、`about two days ago`、`around June` | **整段**跳过，不能匹配内部的 `next month` / `two days ago` |
| 持续时长 | `I finished it in two days`、`for three weeks` | 不标注（是耗时，不是发言日加减） |
| 事件相对 | `three days after the trip`、`the week before the funeral` | 不标注 |
| 引语内 | `she said "I went yesterday"` | 不标注 |
| 已有绝对日期 | `on 2023-05-20 I went yesterday`（同句） | 冲突，整句跳过并记 `status=ambiguous` |
| 时间缺失/非法 | `observed_at` 为空或无法解析 | 跳过 |

---

## 5. 输出状态

> **【评审 R5｜必须补 trace 契约】** 统一 `resolved/ambiguous/skipped` 与 `skip_reason`。不支持项、缺时间等不能直接无痕消失；只有 resolved 才有资格进入正文。解析状态与渲染状态分开：已 resolved 但因预算、去重或原句过长未显示，保留 value/status，另记 rendered=false 和 render_skip_reason。当前 trace 主要是拼接后的 annotation_block，不足以证明跨度正确。
>
> `resolved` 只说明时间短语完成日历映射，不说明该事件已经发生。`It was not yesterday but last month` 里的 yesterday 可以算出日期，却被原句否定。保留完整 source_sentence，不把注释改成未经证实的事件断言。ambiguous 也应保留原短语精度（last month 仍为 month），不能一律写 day。

```text
status=resolved    正常算出 value
status=ambiguous   同句有绝对日期冲突（有 raw_phrase，无 value）
status=skipped     命中不支持类型（只记录，不渲染）
```

**只有 `resolved` 的条目进入证据正文。**

---

## 6. 答题侧应当怎么用（给验证模型看的对照）

> **【评审 R6｜必须修改】** 第一行的 Last Saturday 与第 4 节“星期不标注”不一致。day 例子改用 `yesterday`，星期解析作为独立实验。表中的“会被判错”应改成“按当前严格判分口径有失分风险”，因为本地已出现完全相同答案得到不同判定。
>
> **替换“问题单位优先”的表述**：只有证据支持时才按问题单位展示，且只允许降精度：day→month/year，month→year；不能 year→month 或 month→day。When 保留证据精度；证据只有年月时，问题问哪一天也不能补成 1 日。原始 precision/value 不被问题单位改写。
>
> 不把本节另写进全局答题/评分提示后再声称只改了证据。首轮保持两套提示不变。当前 planner 从全问句找 month，会把 `What year ... month-long workshop?` 误判为 month；若使用 requested_precision，必须从主问开头解析。

| 证据里出现 | 期望答案形式 | 反例（会被判错） |
|---|---|---|
| `precision=day; value=2023-05-20` | `May 20, 2023` 或 `Last Saturday (May 20, 2023)` | `Last Saturday`（未换算） |
| `precision=month; value=2023-06` | `June 2023` | `2023-06-01`（升精度）、`Next month`（未换算） |
| `precision=year; value=2022` | `2022` | `June 2022`（升精度） |

**问题本身指定单位时，以问题为准**：

```text
What month did they go?  → 必须 month 级
What year was that?      → 必须 year 级
What day / When?         → 按证据精度
```

---

## 7. 幂等与预算

> **【评审 R7｜必须全题去重】** 当前 renderer 在每个 row 内重置 seen，同一 source_id/span 出现在两个检索窗口就会重复标注。改为全题 `(source_id, span)` 去重，稳定保留首次出现位置；不要按 value 去重，同日不同事件仍是两条来源。
>
> 去重后计算 8,000 字符预算，超限整题回退无注释 V1。预算由生产与评测共享代码执行；评测不能私自替换某题。新增 source_sentence 也计入预算。幂等不要只检查标题字符串是否在 content 中，否则原文本身出现该标题也会误触发。

- 同一 `(source_id, span)` 只生成一条；重复执行渲染器不得出现第二段标注块。
- 每题新增内容上限 8,000 字符；超限则整题回退（不标注）。
- 标注只追加，不改写原句、不删除其他证据。

---

## 8. 机器可读格式（供程序化验证）

> **【评审 R8｜必须修正示例跨度】** 按本规格给出的原句 `We're thinking about going camping next month`（45 个字符），`next month` 是 **[35,45)**，不是 [58,68)。定义 0 起始、右开、Python Unicode 字符索引；验证 `content[start:end] == raw_phrase`。不能使用 UTF-8 字节位置，也不能以 strip 后文本的偏移定位原文。
>
> 推荐增加 `schema_version`、`source_sentence`、`sentence_span`、`skip_reason`；详细结构见执行方案。展示文本和 JSON 使用同一份结构化对象产生，避免两套日期换算。

```json
{"source_id":"src:9c8327c28393d9ee2837c93a7b113266","raw_phrase":"next month","span":[58,68],"observed_at":"2023-05-25T05:14:00+00:00","anchor_date":"2023-05-25","precision":"month","value":"2023-06","rule":"next_month","status":"resolved"}
```

---

## 9. 验证用例（期望输出）

> **【评审 R9｜保留现有用例，但它们不足以证明新方案】** 本次对当前代码运行了这些 17 例的已有验证脚本，全部通过；脚本主要比较 resolved 的 value/precision，没有完整验证状态、跨度和去重。它们验证了已存在的基础能力，不能作为新增提分证据。新增完整数词/复合相对短语、长引语、否定/计划上下文、跨窗口重复、Unicode span、精度禁止升级等反例；详见执行方案。
>
> **多锚点例子必须改**：原文两个 anchor 同在 5 月，next month 都是 6 月，错误共享锚点也会通过。改成 A=2023-05-08→2023-06，B=2023-06-25→2023-07；另加两个不同日锚点的 yesterday。

| # | observed_at | 原句 | 期望 |
|---|---|---|---|
| 1 | 2024-03-01T10:00:00+00:00 | `I went yesterday.` | day / `2024-02-29` |
| 2 | 2023-03-01T10:00:00+00:00 | `I went yesterday.` | day / `2023-02-28` |
| 3 | 2023-12-31T12:00:00+00:00 | `next month and next year` | month/`2024-01` + year/`2024` |
| 4 | 2023-01-01T09:00:00+00:00 | `I went yesterday.` | day / `2022-12-31` |
| 5 | 2023-07-12T09:00:00+00:00 | `that was two days ago` | day / `2023-07-10` |
| 6 | 2023-05-10T00:00:00+00:00 | `day before yesterday was busy` | day / `2023-05-08` |
| 7 | 2022-07-09T00:00:00+00:00 | `see you day after tomorrow` | day / `2022-07-11` |
| 8 | 2023-10-23T06:00:00+00:00 | `last month was hard` | month / `2023-09` |
| 9 | 2023-05-25T05:14:00+00:00 | `We're thinking about going camping next month` | month / `2023-06` |
| 10 | 2023-05-25T05:14:00+00:00 | `last year I lived in Sweden` | year / `2022` |
| 11 | 2023-05-25T05:14:00+00:00 | `a few years ago I lived there` | 无标注 |
| 12 | 2023-05-25T05:14:00+00:00 | `early next month maybe` | 无标注（整体屏蔽） |
| 13 | 2023-05-25T05:14:00+00:00 | `I finished it in two days` | 无标注（时长） |
| 14 | 2023-05-25T05:14:00+00:00 | `last Friday was fun` | 无标注（星期） |
| 15 | 2023-05-25T05:14:00+00:00 | `she said "I went yesterday"` | 无标注（引语） |
| 16 | 2023-05-25T05:14:00+00:00 | `on 2023-05-20 I went yesterday` | ambiguous（同句绝对日期冲突） |
| 17 | （空） | `yesterday` | 无标注（缺时间） |

**同一批证据里两句不同锚点**：

```text
turn A: 2023-05-08 "next month we travel" → 2023-06
turn B: 2023-05-25 "next month we travel" → 2023-06
```

两条各自独立、各自可溯源，不得共用锚点。
