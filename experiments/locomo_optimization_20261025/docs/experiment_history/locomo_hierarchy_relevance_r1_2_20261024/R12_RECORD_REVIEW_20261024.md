# R1.2 筛选记录与审计材料复核

日期：2026-10-24。只读复核既有文件与离线重建提示词，没有模型调用，没有改动原实验。

## 1. 可以确认的结果

`screen_run.jsonl` 审计时 SHA256：
`C627D94873F5F319E045D3D421EF789DAB2EF1EEE870CE1282707D8237284940`。
文件 28,476 字节，修改时间 UTC 2026-10-24 09:17:10。

- 7 次调用均 HTTP 200，响应模型均为 `deepseek-v4.1-flash`。
- 每次返回 40 个合法且完整的候选 ID；本次没有缺失 ID。
- 280 条判决：direct 0，bridge 6，context 19，unrelated 255；supported 全部 true。
- 离线用原候选池、原问题、原渲染函数重建 7 个提示词，7/7 SHA256 与调用记录一致。
- 7 个请求摘要均显示选择器接受 0、证据变化 false。

注意：“7/7 有效”只说明这 7 次实际返回的数据有效，不等于客户端已完整实现严格 schema 校验。

| 问题 | 已运行臂 | 类别 | 本次最长中心正文 | 被截断中心 |
|---|---|---|---:|---:|
| conv-48#q0033 | L_rel、H_rel | 时间 | 295 字符 | 0 |
| conv-47#q0037 | L_rel、H_rel | 时间 | L 400 / H 311 | 0 |
| conv-42#q0033 | L_rel、H_rel | 时间 | L 249 / H 178 | 0 |
| conv-42#q0017 | 仅 L_rel | 时间 | 342 字符 | 0 |

三次 H 调用各有 16 个 upper 槽，合计 48 个 upper 候选呈现位置。280 不是 280 道独立题，也不是四类问题的抽样。

## 2. 当前样本不适合判断高层是否还有增益

脚本按 `dev_sample.json` 顺序串行执行，中断后留下的是前 4 道题，全部为时间题。没有按四类分层后随机交错执行。后面 76 道没有筛选结果，第四道没有 H 配对结果；`STEP4_SUMMARY.json` 未生成。

前三道已配对题的 B 证据都已含直接时间支持，B 外候选被主动排除这些事实。因此，direct=0 也可能是 B 已覆盖答案后的正常现象，不能仅据此认定高层全局候选质量不足。

具体离线核验：

1. `conv-48#q0033` 问 Deborah 第一次去附近公园晨跑的时间。B 已包含 `fact:3aeaca2287b7e1332c7e05ea4e0129a4`，观察时间 2023-02-25，正文明确有 “yesterday ... morning jog for the first time in a nearby park”，可换算为 2023-02-24。
2. `conv-47#q0037` 问 James 开始上烹饪课的时间。B 已包含 `fact:6f7896333561c7498fefa7aabdf98e60`，观察时间 2022-09-04，正文有 “two days ago ... signed up for a cooking class”，可换算为 2022-09-02。
3. `conv-42#q0033` 问 Joanna 笔记本电脑崩溃时间。B 已包含 `fact:80ca55d1519e91fe95f6ae31e9592a5e`，观察时间 2022-09-14，正文为 “laptop crashed last week”，已有正确周粒度支持。

以上是 B 内直接支持实例，不是新增候选的收益实例。没有再次答题或改变历史分数。

## 3. 中心呈现仍有潜在违约

`screen_client.py:52` 仍执行 `return text[:budget]`，默认 budget=420。中心优先减少了旧版上下文挤占，但未保证任意长度中心完整。本次 280 个中心恰好没有超长，因而实际输入完整；报告中的“不再 text[:420]”不符合当前代码。

现有 7,157 个事实中，按 `[c1] CENTRE: ` 前缀计算，11 个事实超过 420 字符，最长正文 1,016 字符。后续候选可能触发截断。应保留完整中心，只对可选上下文设置预算；超出单次模型预算则减少候选数或分批，不能截断核心事实。

## 4. 筛选含义和接口需要分别修正

### 4.1 supported=true 不代表来源已验证

提示词要求 “the text supports itself”，输入仅含问题和候选正文，没有原文引用或来源文本。这只能测候选自洽性，不能证明候选受原始对话支持。280/280 true 不能作为来源保真率。

来源验证应通过已保存的源引用与原文 span 审计；若该调用只做相关性筛选，字段宜改名或移除 supported，避免形成来源核验已完成的印象。

### 4.2 相关性不等于新增答案支持

筛选器看不到 B，也没有 B 已覆盖的答案值，无法判断候选相对 B 是否新增。它可把完成第一份剧本、向电影节提交剧本之类判为 bridge，但这些并不必然帮助回答第三份剧本何时开始。

应分离三个输出：候选与问题的直接/桥接关系、与已选证据合用的推理路径、候选相对 B 新增的答案值。桥接需指明连接到哪个已有事实，不能仅依赖共同实体或同一主题。

### 4.3 客户端尚未严格拒绝非法输出

代码在 JSON 解析成功后先置 `status=valid`，只额外记录 invalid_ids。未据缺失、重复、未知 ID 或 covers 类型将请求标为 invalid_output；调用者又直接使用原 verdicts。现有 7 次没有这些错误，不应抹去已有有效记录，但后续运行前应完成 schema 契约并保存失败分型。

## 5. 两张审计表目前都是待审材料

- `SUPPORT_AUDIT_20.json`：20 题、S/D/C_L/C_H/B 共 100 个语义状态全为 not_audited；答题评测为 not_run。
- `CANDIDATE_AUDIT_40.json`：40 组、35 道不同问题，支持/相关性/新答案值/替换损失/决定共 200 个状态全为 not_audited。
- 候选表抽样先按 routes 的插入顺序取每题最多前三个候选，再打乱取 40；不是全体候选的均匀抽样。
- JSON 候选正文最多保留 300 字符，Markdown 只展示前 90 字符；正式审计应凭 fact_id 回读完整正文、相关上下文和 B，不能凭截短表格判定。

因此，不能引用这些表支持“lost_in_summary=0 已做语义验证”“40 个候选经盲审均无价值”“摘要丢失未出现”。原词面分桶只能定位，不是语义结论。

## 6. 候选池存在独立于模型能力的实现问题

`step4_run.py` 声明 LOWER_SLOTS=48，但实际上 MAX_CANDIDATES=40；L 取 40 个 lower，H 用最多 16 个 upper 替换，通常仅保留 24 个 lower。48 lower 未实现。

upper 没有按相关性分数全量排序，而是遍历 folded 字典，对 alias 查询词有任意交集即保留第一个 B 外 target，收够 16 个即停。匹配 alias 不能保证它所指的第一个 target 与查询相关，结果受文件插入顺序影响。候选中大量问候、确认和无关主题，可能是这个早停规则造成的，不能归因为层级记忆本身无效。

下一版应先生成并计分所有合规 route-target 对，按目标事实与事件约束评分、去重，再取固定预算。保留匹配 alias、具体 route、target 支持关系、原始 rank、最终 rank，方便区分导航错误和下钻错误。lower 与 upper 的最终预算需明确，不应仅声明未实现的槽数。

## 7. 建议执行顺序

1. **先完成离线语义审计。** 填完 20 题 S/D/C_L/C_H/B，并给每个判断附具体事实/原文引用；完整审 40 组候选，回读完整正文。这一步决定当前库中有没有 B 漏掉、且能被上层导航发现的支持。
2. **修正静态契约与候选顺序。** 完整中心、严格 schema、请求携带 qid/arm 与 fact_id 映射、完整提示词与可恢复响应、真实已实现的槽位和预算；route-target 全量排序后选取，禁止按文件前几个早停。
3. **只对诊断缺口做小样本验证。** 按四类交错执行，覆盖 B 已完整、B 缺事实、B 缺桥接、库内无支持的情况，保持标签只用于离线分层与评估，不用于运行时选证据。至少同时报告候选层召回、相关性精度、新增支持和替换损失，不能只看接受率。
4. **有可核验新增支持再跑配对答题 smoke。** 接受率是管线指标；高接受率不等于更高准确率。若同一候选没有新增支持，应如实归为冗余。
5. **通过后再冻结全量。** 固定代码、提示词、数据与预算；以匹配计算预算的 lower 为对照，报告分类分数、净修复/净退步与独立重复结果。若诊断确实显示原始文本有支持而摘要库没有，再转 R2；若库已有支持但导航找不到，先修召回/下钻；若 B 已有支持而仍答错，则转答案时间换算/推理与判分诊断。

可以封存的是“当前这份具体配置尚无观察到的增益”，不能据 3 个已配对时间题封存整个 L3–L5 方法。也不建议原样继续 80 题后再以同一截断、早停、未审计合同作出方法论判断。

## 附录：可复建提示词与读取文件指纹

以下 7 个提示词由原始问题、事实库、路由与当前代码重新构造，均与原调用记录哈希相等；未再次调用模型。

| qid | 臂 | 原调用 prompt SHA256 = 离线重建 SHA256 |
|---|---|---|
| conv-48#q0033 | L_rel | `588aebfaf4188c12098c3a7ce9abdf13b588b0ed6107653a3415be64d336e406` |
| conv-48#q0033 | H_rel | `5849fdeb888d334e46bf2c103f3e1dbf9f22fbd41098e196a7f85b84608c4d68` |
| conv-47#q0037 | L_rel | `137f8c595121dee3f2107d8cc82b6757bbfa33c440b146af2f752d3d3fe53f4e` |
| conv-47#q0037 | H_rel | `9a0e54802e7b8167e0198ee7968512d765044f1a5ee8a108aa12113bf2aa1928` |
| conv-42#q0033 | L_rel | `f5a507cbea20d3d308e84ad8f887e2d6081cbc874cff343511d04166a39a4da0` |
| conv-42#q0033 | H_rel | `5faf58191eefed08f422d6d046fb58f3d30ff0dd3115af41a1d38b192c5fe823` |
| conv-42#q0017 | L_rel | `996267c178b811491d562502667387f321349a96f8e6ebccadc9f5f36af99f9c` |

以下为本次复核实际读取版本的 SHA256：

- `F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024\screen_client.py`：`eb075dd3c6d351b327b2082bd6076c1931172751caf0bf09a677d9becdc43dd0`
- `F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024\tools\step4_run.py`：`b219c4931b8c30f10838b586c887abff4cb8dd71e4d2b3e1b1891966286e09bc`
- `F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024\screen_run.jsonl`：`c627d94873f5f319e045d3d421ef789dab2ef1eee870ce1282707d8237284940`
- `F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024\SUPPORT_AUDIT_20.json`：`e3da3a35afb6d5af462aa09d03afc0cefbb5857baad0d2c84027c8beae437b06`
- `F:\codex数据库memory\locomo_hierarchy_relevance_r1_2_20261024\CANDIDATE_AUDIT_40.json`：`e937be9efa94cdebc00c3f5efb6ed63ef777c3d1abd2df4bf4e4c0b31d3e9cae`
- `F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024\dev_sample.json`：`9d79da44107a3a2b0029a3415a79d2992ba94e52b8c1c56eb7712b05a1977d5c`
- `F:\codex数据库memory\locomo_hierarchy_relevance_r1_20261024\routes_r1.json`：`c75a7e799f02bd7c435bff1a6fbc705a67efbf8b92cf0b03aab606a0b3d21544`
- `F:\codex数据库memory\locomo_hierarchy_grounded_20261024\store.json`：`1f09619f73a3fb9b8cd7504938fdab9e9bf21d6ae4017d583e54af33ad8d0ae8`
- `F:\codex数据库memory\locomo_temporal_week_20261023\frozen\questions.json`：`0ebfca7be1654b3d34b41e5536838ffef0e287cca5a5782af5d86a37840a142c`
