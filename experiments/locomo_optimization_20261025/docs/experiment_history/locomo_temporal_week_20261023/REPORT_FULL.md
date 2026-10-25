# W0/W1 周表达锚定：全量 861 题结果

- 完成题数：861（incomplete=False）
- B：719/861 = 83.51%
- W0：729/861 = 84.67%
- W1：726/861 = 84.32%

## 1. 分类别

| 类别 | n | B | W0 | W1 |
|---|---:|---:|---:|---:|
| 时间 | 167 | 108 (64.7%) | 118 (70.7%) | 114 (68.3%) |
| 多跳 | 77 | 48 (62.3%) | 48 (62.3%) | 48 (62.3%) |
| 单跳 | 581 | 534 (91.9%) | 534 (91.9%) | 535 (92.1%) |
| 推理 | 36 | 29 (80.6%) | 29 (80.6%) | 29 (80.6%) |

## 2. 预定比较

- **w0-b**：错转对 18，对转错 8，净 +10，McNemar p=0.0755
- **w1-w0**：错转对 8，对转错 11，净 -3，McNemar p=0.6476
- **w1-b**：错转对 16，对转错 9，净 +7，McNemar p=0.2295

## 3. 判定口径

这是固定版本上的全量 861 题对照；未改动的题三组输入逐字节相同并共享同一次采样。

- 离线变更集：128 题（W0 128 / W1 128）
- 保护题改动：0（应为 0）

## 4. 调用与缓存

- 唯一答题请求 1115；判分请求 968
- 逻辑结果 2583（= 题数 × 3）
- 缓存只在本次 replicate 内生效；未改动题的三组输入完全相同，因此共享同一答案。

## 3. 解释与判定

### 3.1 主比较

- W0 − B：时间 108 → 118（**+10**），总 719 → 729（+10）
- McNemar 精确双侧 p = 0.0755；按对话聚类 bootstrap 95% 区间 [+0.00, +2.44] pp
- 其他三类：多跳 48→48、单跳 534→534、推理 29→29，均未下降
- 判定：**方向明确、达到方案设定的 +5 工程目标，但未达统计显著（p>0.05）**，按方案口径称为探索性结果

### 3.2 W1 的区间没有带来增益

- W1 − W0：错转对 8，对转错 11，净 −3（p=0.65）；时间 118 → 114
- 说明在 W0 已给参照日期后，再附加精确区间反而引入噪声（模型可能挑错区间或过度具体化）
- 建议：本轮只采用 W0；区间（W1）不进入下一步

### 3.3 样例

W0 修好 18 题、弄坏 8 题。

| qid | 金标 | B | W0 |
|---|---|---|---|
| conv-26#q0005 | The saturday before 25 May 2023 / May 20, 20 | Last Saturday | Last Saturday (2023-05-20) |
| conv-26#q0008 | The week before 9 June 2023 / From May 29, 2 | Last week. | The week before June 9, 2023 |
| conv-26#q0031 | The Tuesday before 20 July 2023 / July 18, 2 | Last Tues | July 18, 2023 |
| conv-30#q0011 | March, 2023 / the week before 16 March, 2023 | Last week. | The week before March 16, 2023 |
| conv-30#q0026 | June 2023 / the week before 21 June, 2023 | Last week | The week before June 21, 2023 |
| conv-30#q0028 | July 2023 / the week before 9 July, 2023 | Last week | The week before July 9, 2023 |

退步样例：

- conv-30#q0010 | gold=A few years ago
  - B : A few years ago.
  - W0: A few years before 2023-02-08.
- conv-41#q0038 | gold=Around August 2022
  - B : Around August 2022.
  - W0: About a year before August 3, 2023
- conv-42#q0051 | gold=The Saturday before 7 November, 2022 / 5 November 
  - B : 2022-11-05
  - W0: Last Saturday
- conv-43#q0031 | gold=November, 2023.
  - B : Early November 2023.
  - W0: The week before November 6, 2023

### 3.4 审计修正（方案第 10 节）

- 运行时 hash 复验：evaluate 启动时重算题单/候选/源库/A0/臂/模板 hash，与 experiment.json 不一致即拒绝运行（本轮已实际拦截过一次过期 hash）。
- 计数与缓存：`results.partial.json` 持久化累计 counters；本轮 1,115 个唯一答题请求、968 个唯一判分请求，逻辑结果 2,583。
- 每条结果记录 `answer_request` / `judge_request` hash 与 `answer_shared` 标记。
- judge 解析失败不进入缓存：重试同一答案的判分，最多 3 次，仍失败则单独记录 `judge_parse_error`。
- 报告区分：缓存一致性由共享采样保证；judge 的独立重复稳定性本轮未测（需预登记诊断集，另报）。

### 3.5 建议

1. **采用 W0**（C1 + `last week`/`last weekend` 参照日期），放弃 W1 的区间字段。
2. 下一步优先做 **weekday 消歧**（方案第 7 节指出最近星期一/下一星期六都不能一刀切），再考虑事件召回。
3. 若要提高显著性，需在固定版本上做第二次独立 replicate（重建缓存），而不是挑题重跑。
