# R1.2 执行状态（步骤 1–3 准备）

日期：2026-10-24
目录：`F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024`
依据：`CODEX_NEXT_EXECUTION_PLAN_R1_2_20261024.md` 第 6 节步骤 1–3

---

## 1. 步骤 1：封存（已完成）

`RUN_MANIFEST.json`：11 个输入/代码文件哈希（store / index / routes / events / questions /
dev_sample / P0 与 P1 脚本 / support_atoms / controls），缺失 0。

同时记录两条“不可重现”声明：

- 早期 token 选择器版本没有独立快照，标记 not reproducible；
- `P1_MODEL_SCREEN.json` 从未落盘，历史 51 次调用一律视为**未验证**，不进入任何结论。

## 2. 步骤 2：筛选客户端修复 + 有限自检（已完成）

`screen_client.py` 修复项：

| 方案要求 | 实现 |
|---|---|
| Bearer 鉴权 | `Authorization: Bearer <key>`，从现有配置读取，不打印 |
| 错误分类 | `auth_error / http_error / network_error / json_error / valid / not_run` 分开 |
| 结果落盘 | 每次调用写 `screen.jsonl`（含 prompt 哈希、候选 ID、状态、用量） |
| 严格 JSON | `supported` 必须是真布尔；`relevance` 必须属于枚举；缺失 ID 记 `invalid_ids` |
| 中心可见 | 候选渲染改为 **CENTRE 优先**，上下文只在剩余预算内追加，不再 `text[:420]` 截断 |
| 代理干扰 | `trust_env=False`（首次自检曾因代理返回 network_error，已修复） |

**自检结果（4 次模型调用，未超 5 次上限）**

| 检查 | 结果 |
|---|---|
| 连接 + JSON 契约 | valid |
| 正例（明确支持的游戏推荐） | valid，c1 被识别 |
| 负例（同人物不同事件） | valid，判为 unrelated |
| 鉴权失败 | **auth_error**（不再当成负判决） |
| 离线契约（loose bool / 非法 relevance） | 均被拒绝 |

产物：`PIPELINE_PREFLIGHT.json`。

## 3. 步骤 3 准备：20 题语义来源核对表（已生成骨架）

- 抽样规则：12 道 `absent_everywhere`（时间 4 / 推理 4 / 多跳 3 / 单跳 1）
  + 8 道 `in_facts`（每类 2），种子 `20261024`；
- 每题已附：问题、金标、诊断桶、原文消息数、B 事实数与两个词面重合度；
- `S / D / C_L / C_H / B` 五层状态暂标 `not_audited`，等待人工核对；
- 说明明确写了“未核对项不得外推为全量结论”。

产物：`SUPPORT_AUDIT_20.json` / `.md`。

---

## 4. 当前未完成（按方案顺序）

| 步骤 | 内容 | 状态 |
|---|---|---|
| 3 | 20 题五层状态人工核对 | 待做（骨架已就绪） |
| 3b | ≥40 组候选盲审表 | 未做 |
| 4 | 冻结一版并跑完 80 题 L_rel / H_rel 筛选 | 未做 |
| 5 | 分支决定（全量 / 改高层索引 / R2 / 封存） | 未做 |

---

## 5. 下一步

1. 用 `screen_client.py` 对 20 题逐题生成候选（中心可见 + 支持上下文），人工填写五层状态；
2. 产出 40 组候选盲审表（隐藏 arm 与历史判分）；
3. 冻结筛选提示与预算，跑完 80 题两臂；
4. 按方案第 5 节表格做一次性分支决定。

---

## 6. 步骤 3 交付（材料已就绪，判断待人工）

| 产物 | 内容 | 状态 |
|---|---|---|
| SUPPORT_AUDIT_20.json/md | 固定抽样 20 题；含问题、金标、诊断桶、原文/B 词面重合度；五层状态 not_audited | 材料就绪 |
| CANDIDATE_AUDIT_40.json/md | 40 组 B 外真实候选（35 道题）；arm 与历史分数已隐藏；判断字段 not_audited | 材料就绪 |

说明：这两份是**审计材料**，不是审计结论。按方案要求，S/D/C_L/C_H/B 五层状态和 40 组的
支持/相关性/新答案值/可替换判断必须人工填写；自动化只能给出候选 ID、文本与词面重合度。

## 7. 下一步

1. 人工填写两份审计表的判断字段；
2. 用 screen_client.py 在冻结配置下跑完 80 题两臂筛选（每臂至多一次调用，结果逐题落盘）；
3. 按方案第 5 节表格做一次性分支决定。
