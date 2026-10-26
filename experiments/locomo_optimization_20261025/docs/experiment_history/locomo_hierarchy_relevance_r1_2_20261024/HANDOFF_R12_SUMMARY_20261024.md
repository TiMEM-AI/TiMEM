# R1.1 → R1.2 执行结果汇总（供下一步规划）

日期：2026-10-24
执行：DeepSeek Harness
性质：离线审计为主；R1.2 共 11 次筛选模型调用（4 次自检 + 7 次步骤 4），未重新答题/判分。

---

## 1. 轮次与目录

| 轮次 | 目录 | 内容 |
|---|---|---|
| R1 dev smoke | `locomo_hierarchy_relevance_r1_20261024` | L 56/80、H 56/80，净 0（旧实现，已被复核指出缺陷） |
| R1.1 P0 | `..._r1_1_20261024` | 证据链修复 + 契约审计 |
| R1.1 P1 | 同上 | 三种选择器（token / 实体关系槽位 / 模型筛选） |
| 事实诊断 | 同上 | 金标词面覆盖的 S/D 分桶 |
| R1.2 | `..._r1_2_20261024` | 支持单元、筛选客户端修复、自检、审计表、步骤 4 |

---

## 2. R1.1 P0（已通过）

| 项 | 值 |
|---|---|
| 题数 | 80 |
| 证据改变 | 0 |
| 纯删除 | **0** |
| 目标未呈现 | **0** |
| 直接日期保护 | 46 题（`not compositional_query()`） |
| 结论 | 证据链正确性通过；高层未产生任何变化 |

---

## 3. R1.1 P1（三种选择器）

| 选择器 | 证据改变 | 接受 | 主要拒绝 |
|---|---:|---:|---|
| token 槽位 | 1/80 | 2 | no_new_slot 3.9–4.0 万 |
| 实体/关系/类型槽位 | **0/80** | **0** | no_new_slot 3–4 万 |
| 模型筛选（旧脚本，无鉴权） | 已跑 51 题 | 0 | **结果不可信**（无 Bearer、无错误分类） |

关键结论：折叠把 16,950 条路由压到 6,581 组，消除了重复投票，但也让高层候选与 B 同源。

---

## 4. 事实来源诊断（79 题有效）

| 桶 | 题数 |
|---|---:|
| in_facts | 36（46%） |
| **lost_in_summary** | **0** |
| absent_everywhere | 43（54%） |

局限：词面覆盖无法识别同义改写/跨句组合；`absent_everywhere` 不等于“库里没有信息”。

---

## 5. R1.2 已完成

### 5.1 封存

`RUN_MANIFEST.json`：11 个文件哈希，缺失 0。
明确声明：早期 token 选择器无快照（不可重现）；旧 `P1_MODEL_SCREEN.json` 从未落盘（未验证）。

### 5.2 支持单元（support atom）

```text
(fact_id, conv, observed_at, subjects, relation, values, kind)
选择：gain = support(cand) − support(selected)
      loss = support(victim) − support(selected−victim+cand)
接受：gain≠∅ 且 loss=∅
```

六项功能控制全部通过（新答案值接受、尾部目标可见、安全替换、同人不同事件拒绝、
无安全替换拒绝、无效候选拒绝）。注意：控制④ 的拒绝来自结构性保护，**关系匹配尚未接入**。

### 5.3 筛选客户端修复 + 自检

| 要求 | 实现 |
|---|---|
| Bearer 鉴权 | ✓（曾因代理报 network_error，已加 `trust_env=False`） |
| 错误分类 | auth/http/network/json/valid/not_run |
| 逐次落盘 | `screen.jsonl` |
| 严格 JSON | 真布尔 + 枚举 + 缺失 ID 记录 |
| 中心可见 | CENTRE 优先渲染，不再 `text[:420]` |

自检 4 次调用：连接、正例、负例、鉴权失败分类、离线契约 —— 全部符合预期。

### 5.4 审计材料

- `SUPPORT_AUDIT_20.md/json`：12 道 absent + 8 道 in_facts，五层状态 not_audited；
- `CANDIDATE_AUDIT_40.md/json`：40 组 B 外真实候选，arm 与历史分数隐藏，判断字段 not_audited。

### 5.5 步骤 4（部分完成）

| 项 | 值 |
|---|---|
| 已跑 | 3.5/80 题（L_rel 4 + H_rel 3） |
| 有效调用 | 7/7 |
| 单次耗时 | 40–60 秒（40 候选） |
| 判决分布（280 条） | unrelated 255（91%）、context 19、bridge 6、direct 0 |
| supported=true | 280/280 |
| 选择器接受 | **0** |
| 证据变化 | 0 |

---

## 6. 核心结论（有数据支撑）

1. **筛选链路技术上已可用**（7/7 有效、中心可见、字段合法）。
2. **按“查询词重合”召回的 B 外候选，91% 被判无关**；剩余 bridge 又无新增支持单元或无可安全替换尾部。
3. **问题在候选质量，不在选择器严格程度**：token、槽位、模型三种判据都指向同一结论。
4. **摘要丢失在当前样本中未出现**（lost_in_summary=0），R2 的必要性尚无证据支持。

---

## 7. 请下一步规划回答问题

1. **候选生成是否应改为结构化检索**（实体/关系/时间约束，或向量召回），
   而不是“B 外事实按查询词重合排序”？
2. **80 题步骤 4 是否继续？** 若继续，建议把每题候选降到 12 个（约 15 秒/次，20–30 分钟完成），
   还是直接按方案第 5 节分支 D 封存“当前配置无可测增益”？
3. **support atom 的取值方式是否足够？** 当前 `values` 来自数字/年份/首字母大写短语，
   可能漏掉同义改写；是否需要模型抽取或别名词典？
4. **“无增益”的判定门槛**：需要多少题、什么接受率、什么配对结果，才允许封存该配置？

---

## 8. 产物索引

```text
F:\codex数据库memory\locomo_hierarchy_relevance_r1_1_20261024\
  P0_EVIDENCE_AUDIT.json/md, P1_SELECTOR_AUDIT.md, P1_SELECTOR_SUMMARY.json,
  P1_CONCLUSION.md, P1_FACT_LOSS.json/md, requests/p1/{B,H_raw,H_fold,L_rel,H_rel}/

F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024\
  RUN_MANIFEST.json, PIPELINE_PREFLIGHT.json, CONTROLS_RESULT.json,
  SUPPORT_AUDIT_20.md/json, CANDIDATE_AUDIT_40.md/json,
  STEP4_STATUS.md, screen_run.jsonl, requests/step4/{L_rel,H_rel}/
  support_atoms.py, screen_client.py, tools/{run_manifest,selfcheck,support_audit_20,candidate_audit_40,step4_run}.py
```
