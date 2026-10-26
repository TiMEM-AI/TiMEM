# R1.3 控制与“完成审计”的只读复核

日期：2026-10-24。范围：`controls_r13.py`、`audit_fill.py`、两份 `*_COMPLETED.json`、冻结原始来源和事实库。没有网络、模型调用或旧文件修改。

## 结论

满 24 窗口的安全替换确实发生：移除 `t2`、新增 `cand`，窗口仍为 24。其余结论应限于这组人工构造词面样例。`16 complete / 4 uncertain` 和 `related_new=0` 是确定性词面规则的输出，尚不能作为来源充分性、摘要无损或高层没有新增答案支持的语义证明。

## 六项控制

- 安全样例实际移除 `t2 = Jolene recommended Zelda to Deborah again.`，它与 `t1` 的支持集合重复；注释所说“tail 4 duplicates tail 3”不符，但真实替换本身成立。
- `passed` 只聚合前五项 `ok`。角色交换仅记录 `accepted=0`，没有 `ok`，未纳入通过条件。不能严格称“六项全部通过”。
- “不安全替换”中的不同事件，仍留下大小写实体原子，例如 `April`、`Shanghai`；因此拒绝不等于已经证明保留了问题所需的四个独有答案值。
- `_agent` 只绑定关系词前最近的大写词，不检查收件人。只读反例：问 `Which games did Jolene recommend to Deborah?`，候选 `Jolene recommended Overcooked 2 to Maria.` 会被接受。相同游戏附加 `on Monday` 或 `in 2022` 也会产生“新答案值”增量；`Jolene suggested Overcooked 2 to Deborah.` 却被拒绝为 `no_relation_gain`。
- 支持原子的 `subjects` 包含全部大写单词（人、游戏、月份等），任何集合变化会改变其他原子的键。因此其 gain/loss 还不是稳定的 `(施事, 关系, 受事, 答案值)` 比较。

## 三个来源复核案例

### conv-26#q0044：做陶盘的时间

问题：`When did Melanie make a plate in pottery class?`，目标时间 2023-08-24。

完成审计的首条来源 `src:2a5f556f7d05e2ac6602998ce69917d9`（2023-05-25）全文为感谢 Melanie 并说要 `make sure these kids have a safe and loving home`，无陶盘事件。其余列出的两个 S 命中也是保重心理健康、蓝色花瓶；所列 D/C_L 引用同样不是陶盘支持。

真实可核对链为：

- `src:570f543cb8a8411695f4c00e311a0b79`（2023-08-25）：`That plate is awesome! Did you make it?`
- `src:ae0f239c1037e7d0486ad4b4b9c7cd0f`（同观察时间）：`Yeah, I made it in pottery class yesterday.`
- `fact:182f4bde298fa4b9097008ec35fb3c47`：Melanie said she made the plate in pottery class yesterday（同观察时间）。

这里可证明原始和派生事实确有支持，但当前 `complete` 的引用没有证明这一点。S 的说话人/代词需要邻接上下文解析；不能以单句是否出现 Melanie 判定来源不存在。

### conv-48#q0070：卡牌游戏

问题：`What card game is Deborah talking about?`，参考答案 Exploding Kittens。

完成审计所列 S/D 是父亲去世、问候和《Sapiens》，C_H 是信件、瑜伽、旧居回忆。它们不能支撑卡牌游戏答案。

真实相关来源是 `src:382d70ebffb44fc6947adecf55489ec8`（2023-09-12）：关于猫、轮流抽卡、攻击对手的卡牌游戏描述。它没有直接说出游戏名；本题需要根据描述推理。因此审计应记录“描述支持 + 推理需求”，不能强求直接包含答案字符串，也不能把问候当作 complete。

### conv-41#q0010：祖母去世时间

问题：`When did Maria's grandmother pass away?`。

完成审计所列 S 是 2022-12-17 的旅行、踢拳和政治，D/C_L 是问候、旅行和学校建设；不涉及祖母。

真实相关来源 `src:7c27a6321a34f82875534ac664efd7d1`（2023-03-06）：`Last week, my grandma passed away`。需要结合说话人身份、`grandma/grandmother` 同义及观察时间解释。现有关系词表未识别 `pass away`，遂退化为只命中 Maria，生成无关的 complete。

上述核查没有把三个题的所有 B 和高层候选重新语义标注，故不据此断言 B 缺失或高层可补。这里只证明现有审计状态与引用不足以支持原结论。

## 两份“完成”文件的真实口径

`audit_fill.py` 的 `covers` 只检查实体交集和关系词词干交集；关系集合为空时只要实体即可。`s_state/d_state` 有任何这种命中就 complete，`overall` 只看 S/D 是否命中。即使 C_H 和 B 无关，也不会改变 overall。`temporal` 只记录，不用于事件/时间过滤。引用只保留插入次序的前三条，不是答案相关性的前三条。

40 候选文件仍保留原 `audit` 中六类 `not_audited` 项；40/40 的 `decision` 都是 `not_audited`。新增的 `auto_class` 把“实体+关系词出现”命名为 `related_new`，完全未比较 B 的答案值，也未检查 supported、角色、否定、时间约束或 victim 的损失。`related_new=0` 只说明该规则未双命中，不能推出没有相关增量。反过来，37 个 needs_context 也可能只是无关问候。

## 最小修正建议

1. 将现有两份文件标作 `lexical_prefill`，保留数字但撤回语义完结含义；不要为此新增一轮大规模模型实验。
2. 在同一固定 20 题/40 候选上补真正的离线语义审核：原文片段与上下文、角色绑定、事件、答案值/推理所需线索、观察时间和事件时间、相对 B 的新增支持、被替换窗口的必要支持。无法确定就 uncertain，不用词面未命中表示 source missing。
3. 控制先新增“同施事不同受事”“同答案值不同附加日期”“同义关系”三个最小反例；给角色交换增加断言并纳入 `passed`。不必立即构建全套事件图。
4. 后续窗口重排的收益应以最终呈现的答案相关支持逐条复核。来源审计用于诊断，不向运行时选择器注入 gold 或以上人工答案。

