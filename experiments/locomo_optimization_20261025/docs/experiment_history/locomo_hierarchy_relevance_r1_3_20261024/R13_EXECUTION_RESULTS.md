# R1.3 执行结果（交下一步规划）

日期：2026-10-24
目录：`F:\codex数据库memory\locomo_hierarchy_relevance_r1_3_20261024`
执行范围：R1.3 方案阶段 A（审计 + 修正控制）与阶段 B 的 12 窗口联合重排（部分完成）
模型调用：4 次控制外自检不计；阶段 B 共 **12 次调用（11 valid、1 network_error）**，未重新答题/判分。

---

## 1. 已完成并交付

### 1.1 问题条件选择器 + 满容量控制（阶段 A）

`controls_r13.py` / `CONTROLS_R13.json`，6 项全部通过：

| 控制 | 结果 |
|---|---|
| 满容量安全替换（24 窗口，尾部仅重复支持） | replaced=1，窗口仍 24 |
| 满容量不安全替换（4 个尾部均有独有支持） | accepted=0，证据不变 |
| 同人不同事件 | 拒绝（relation 不匹配） |
| 新答案值（同人不同游戏） | 接受，gain 非空 |
| 否定不当作同一支持 | 拒绝（polarity_mismatch） |
| 角色交换 | 拒绝（agent_mismatch） |

相对 R1.2 的修正：选择器接收问题（relation/negated/agent）；增量必须含关系绑定原子；
只比较问题相关支持，不再要求保留 victim 全部原子；关系词做词形归并。

### 1.2 二十题来源支持审计（问题条件检索）

`SEMANTIC_AUDIT_20_COMPLETED.json` / `.md`

```text
complete  16 / 20
uncertain  4 / 20   （S 或 D 在检索范围内未命中，已标注搜索范围）
```

规则：以问题实体 + 关系词检索；S/D 需同时命中；C_H 取折叠路由中 B 外目标；B 取命中事实是否在 B。
每行引用 source_id / fact_id / 观察时间。

### 1.3 四十组候选审计

`CANDIDATE_AUDIT_40_COMPLETED.json`

```text
needs_context     37   （只共享实体，缺关系或答案值）
binding_uncertain  1
unrelated          2
related_new        0   ← B 外候选中没有"实体+关系"都匹配的新答案值
```

### 1.4 阶段 B：12 窗口联合重排（部分完成）

- 协议：L_rel = 12 个低层候选；H_rel = 8 低层 + ≤4 高层唯一候选；
- 每题一次调用，输入含 B 前 20 窗口、尾部 4 窗口、12 个候选（中心全文）；
- 均衡抽样：每类 5 题、种子 20261027。

已完成的调用（11 valid）：

| 指标 | 值 |
|---|---:|
| 调用数 | 12（11 valid，1 network_error） |
| selected_optional_window_ids 合计 | 5 |
| removed_base_tail_ids 合计 | 12 |
| evidence_decisions 中 relevance | unrelated 83 / context 21 / direct 7 / bridge 1 |
| constraint_mismatch=true | 4 |

逐题结果：

```text
conv-30#q0007  L_rel: selected=0 removed=2 changed=True
conv-30#q0007  H_rel: selected=0 removed=0 changed=False
conv-26#q0044  L_rel: selected=1 removed=2 changed=True
conv-26#q0044  H_rel: selected=1 removed=2 changed=True
conv-49#q0040  L_rel: selected=1 removed=1 changed=True
conv-41#q0004  L_rel/H_rel: selected=0 removed=0 changed=False
conv-48#q0033  L_rel/H_rel: selected=0 removed=0 changed=False
conv-48#q0117  L_rel: selected=1 removed=4 changed=True
conv-48#q0117  H_rel: selected=1 removed=1 changed=True
```

这是本系列中**首次由我方实现产生真实证据变化**（5 个可选窗口被选入、12 个尾部窗口被移除）。
但调用速率约 80 秒/次，40 次调用需约 50 分钟，本轮在 12 次后停止。

---

## 2. 未完成

| 项 | 状态 |
|---|---|
| 阶段 B 剩余 9 题（18 次调用） | 未跑 |
| 两臂证据冻结与哈希清单 | 未做（当前仅存 JSONL 与部分行） |
| 20 题答题/判分 | 未做（按协议本轮不答题） |
| 扩到 80 题或全量的决定 | 未做 |

---

## 3. 本轮新增产物

```text
controls_r13.py / CONTROLS_R13.json          问题条件选择器 + 六项控制
audit_fill.py                                 问题条件检索 + 两份审计填写
SEMANTIC_AUDIT_20_COMPLETED.json / .md       20 题五层状态（16 complete / 4 uncertain）
CANDIDATE_AUDIT_40_COMPLETED.json            40 组候选自动分类（related_new=0）
stage_b.py                                    12 窗口候选池 + 联合重排
screen_stageB.jsonl                           12 次调用的原始响应与解析
STAGE_B_SUMMARY.json                          阶段 B 汇总（部分）
```

## 4. 数据说明

- 阶段 B 的 1 次 `network_error` 已按错误分类记录，未计入语义判决；
- `screen_stageB.jsonl` 曾因换行写入缺陷导致行合并，已修复为合法 JSONL；
- 所有金额/哈希与调用元数据在 JSONL 中逐条保留（prompt 哈希、模型名、usage）。
