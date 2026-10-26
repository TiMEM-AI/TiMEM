# R1.3b 执行结果（交下一步规划）

日期：2026-10-24
目录：`F:\codex数据库memory\locomo_hierarchy_relevance_r1_3b_20261024`
依据：`CODEX_R13_REVIEW_AND_NEXT_PLAN_20261024.md`
模型调用：18 个计划筛选任务 + 1 次重试 = 19 次调用（17 valid、2 network_error）；**未答题/判分**。

---

## 1. 已完成

### 1.1 冻结与准备（离线）

| 项 | 值 |
|---|---|
| 固定 20 题 | 原种子 20261027，四类各 5 题，与 R1.3 同一批，未换题 |
| 路由保护 | `not compositional_query()` → **11 题 guard_skip**（两臂逐字回退 B，不产生调用） |
| 可干预题 | **9 题**（18 个逻辑任务） |
| 每题 B 证据 | `evidence/B/*.json`（完整文本 + 窗口 + 哈希） |
| 请求快照 | `requests/{qid}_{arm}.json`：protected/tail/optional 均为**完整窗口序列化**（含 actual_fact_id、全文、观察时间），不再用裸哈希 ID |
| 任务清单 | `tasks.jsonl`：planned 18 / guard_skip 22 |
| 契约测试 | `CONTRACT_TESTS.json`，`passed: true` |

### 1.2 契约测试（与生产路径共用函数）

| 测试 | 结果 |
|---|---|
| 合法 1 换 1（24 窗口） | 通过，窗口数 24、中心事实呈现 |
| 纯删除（selected=0/removed=2） | 拒绝 |
| 未知 optional ID | 拒绝 |
| 删除受保护窗口 | 拒绝 |
| 数量超额/重复 ID | 拒绝 |
| swap 数量不匹配 | 拒绝 |
| constraint_mismatch | 拒绝，整次回退 |

### 1.3 筛选执行（18 个计划任务）

| 项 | 值 |
|---|---:|
| 计划任务 | 18 |
| 终态 valid | 16 |
| network_error（重试后回退 B） | 2 |
| 校验失败回退 B | 2 |
| 实际调用 | 19（含 1 次重试） |
| selected_optional_window_ids 合计 | 4 |
| removed_base_tail_ids 合计 | 4 |
| 证据文件（L/H） | 14 |
| 相对 B 发生变化的臂证据 | **2** |

（2 个 network_error 任务没有最终证据文件，按协议标记 fallback B。）

---

## 2. 未完成

| 项 | 状态 |
|---|---|
| B/L/H 三组 20 题答题 | **未执行** |
| 判分与分类结果 | **未执行** |
| `EXCHANGE_REVIEW`（逐次交换语义复核） | 未做 |
| `RESULTS_20.md/json` | 未产出 |
| 扩大/封存决定 | 未做 |

---

## 3. 产物索引

```text
r13b_prepare.py          冻结 20 题、B、唯一候选池、完整序列化、契约测试、tasks
r13b_run.py              计划任务执行器（校验 + 原子应用 + 断点状态）
RUN_MANIFEST.json        20 题、B/L/H 请求与证据哈希
CONTRACT_TESTS.json      契约测试（passed: true）
tasks.jsonl              40 个逻辑任务终态（18 planned 已执行，22 guard_skip）
requests/*.json          每题每臂完整请求快照
evidence/B/*.json        公共低层证据与哈希
evidence/{L,H}/*.json    已应用的最终证据（14 份）
responses.jsonl          19 次调用的原始响应、prompt 哈希、模型名、usage
state.json               断点状态与逐任务结果
```

---

## 4. 执行中修复的缺陷

1. `apply_plan` 曾把窗口标签（c1）当作事实 ID，导致 `render` KeyError；已改为短标签→真实 fact_id 映射。
2. `responses.jsonl` 写入时用了字面 `\n`，导致行合并；已修复为合法 JSONL。
3. 2 次 network_error 按协议重试 1 次后回退 B，未计入语义判决。

---

## 5. 下一步所需输入

按方案第 5 步第 6 节，剩余闭环为：

1. 逐次交换语义复核（4 次 selected / 4 次 removed，共 2 份变化证据）；
2. B/L/H 三组各 20 题答题与判分（B 与 L/H 同证据时共享响应，预计 ≤60 条答案 + ≤60 条判分）；
3. 输出分类与总分、L−B、H−L 配对、调用与失败统计；
4. 按第 6 节表格做一次扩大或封存决定。
