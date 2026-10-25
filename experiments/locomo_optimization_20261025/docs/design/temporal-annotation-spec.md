# 时间证据标注规格（day / month / year 精度）

> 用途：给答题模型提供"已算好的日历值"，避免它自己做相对→绝对换算。
> 本文是可独立验证的规格：任何人（或模型）拿到输入原句，都应当能唯一确定输出。

---

## 1. 数据前提

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

```text
Original conversation evidence:
[2023-05-25T05:14:00+00:00] assistant: We're thinking about going camping next month

Calendar annotations derived from the original turns:
- source_id=src:9c8327c28393d9ee2837c93a7b113266; raw_phrase="next month"; observed_at=2023-05-25T05:14:00+00:00; anchor_date=2023-05-25; precision=month; value=2023-06; rule=next_month; status=resolved
```

字段顺序固定，分号分隔，值不加空格（`value=2023-06` 不是 `value = 2023-06`）。

---

## 3. 精度与值格式（核心表）

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

```text
status=resolved    正常算出 value
status=ambiguous   同句有绝对日期冲突（有 raw_phrase，无 value）
status=skipped     命中不支持类型（只记录，不渲染）
```

**只有 `resolved` 的条目进入证据正文。**

---

## 6. 答题侧应当怎么用（给验证模型看的对照）

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

- 同一 `(source_id, span)` 只生成一条；重复执行渲染器不得出现第二段标注块。
- 每题新增内容上限 8,000 字符；超限则整题回退（不标注）。
- 标注只追加，不改写原句、不删除其他证据。

---

## 8. 机器可读格式（供程序化验证）

```json
{"source_id":"src:9c8327c28393d9ee2837c93a7b113266","raw_phrase":"next month","span":[58,68],"observed_at":"2023-05-25T05:14:00+00:00","anchor_date":"2023-05-25","precision":"month","value":"2023-06","rule":"next_month","status":"resolved"}
```

---

## 9. 验证用例（期望输出）

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
