# R1.1 事实来源诊断复核

日期：2026-10-24。范围：只读核查 `tools/p1_fact_loss.py`、`P1_FACT_LOSS.json`、冻结原文 SQLite、派生事实库与 P1/B 最终证据；未调用网络或模型，未修改既有实验结果。

## 判断

`lost_in_summary=0` 的计算结果可以复现其定义，但该定义只是“某条原文的金标内容词重合率达到 60%，且任一派生事实未达到 60%”。它不能证明摘要没有丢信息，也不能把 `absent_everywhere=43` 解释成原文和派生库都不支持答案，更不能用来判断 B 最终证据是否缺失。

本次核验的四个被标记 `absent_everywhere` 的样本，原文、派生事实库、B 最终证据中均已有可用支持。这里的“可用支持”按语义、时间锚与必要上下文判断；不要求答案字符串在任一单条文本中直接出现。这是反驳当前诊断解释的案例核查，不是对全部 43 题的语义重标注，不能外推四例的发生比例。

## 原始数据字段核查

- `source_turns.content` 在当前冻结数据库中确实是纯文本原话；全部 5,882 条均为字符串。脚本没有把 JSON 包装、错误的 `text` 字段或工具日志当原文。
- 数据库有 `role`、`observed_at`、`user_id`、`session_id` 等列。诊断只读取 `content`，没有读取说话角色或时间锚。因此它不判断某句话是否属于正确的人、正确事件，也不能把 `yesterday` 换算成金标日期。
- 原文的 `role=user/assistant` 是数据导入时的人物映射；不能只凭角色字面把用户身份等同于数据集中的人物姓名。部分派生事实仍用 `User/Assistant`，语义审计需要结合会话人物映射、同观察时间的相邻原话。
- 过滤使用 `user_id LIKE '%:conv-N%'`。当前 10 段对话中逐一检查，这个过滤都只命中对应会话，没有发现 `conv-26` 误混 `conv-260` 等串号；下一版应使用显式映射或精确 `user_id = ?`，并断言每会话记录数。
- 当前每会话原文数：conv-26 419、conv-30 369、conv-41 663、conv-42 629、conv-43 680、conv-44 675、conv-47 689、conv-48 681、conv-49 509、conv-50 568，总计 5,882。

## 四个典型反例

以下 B 均指 `requests/p1/B/<conv>_<qid>.json` 已保存的最终 `evidence`，不是全库可检索性。

### 1. 时间：conv-48#q0033

问题：When did Deborah go for her first morning jog in a nearby park? 金标：24 February, 2023。

原诊断：`absent_everywhere`；best_fact_cover=0.50、best_source_cover=0.00。

- 原文 ID：`src:fb215feee997d128725a675059a2a8ff`，观察时间 `2023-02-25T08:50:00+00:00`，user。原话含“yesterday I went for a morning jog for the first time in a nearby park”。
- 派生事实：`fact:3aeaca2287b7e1332c7e05ea4e0129a4`，同观察时间，保留 Deborah、first morning jog、nearby park、yesterday。
- B 最终证据包含该事实及其观察时间。
- 支持判断：2 月 25 日的 yesterday 对应 2 月 24 日；原文和派生事实均可支持金标，B 也已经召回。原文日期内容词覆盖为 0 不表示缺少证据。该例的下一步应检验时间换算或答题表现，不能归类为召回缺失。

### 2. 时间与指代：conv-26#q0044

问题：When did Melanie make a plate in pottery class? 金标：24 August 2023。

原诊断：`absent_everywhere`；两项覆盖均为 0.00。

- 原文 ID：`src:570f543cb8a8411695f4c00e311a0b79`，`2023-08-25T05:33:00+00:00`，user：“That plate is awesome! Did you make it?”
- 紧接原文：`src:ae0f239c1037e7d0486ad4b4b9c7cd0f`，同观察时间，assistant：“Yeah, I made it in pottery class yesterday.”
- 派生事实：`fact:78572782001a2f5fee69b232457b5231` 保留 plate 提问；`fact:182f4bde298fa4b9097008ec35fb3c47` 明确改写为 Melanie made the plate in pottery class yesterday。
- B 最终证据包含这两条及时间锚。
- 支持判断：对象指代和昨日时间均有支持，8 月 25 日的 yesterday 为 8 月 24 日。单条原文与金标日期字符串不重合完全正常。此例还说明语义支持可能需要两条原文，不能仅用单条阈值诊断摘要丢失。

### 3. 推理：conv-43#q0018

问题：Which popular time management technique does Tim use to prepare for exams? 金标：Pomodoro technique。

原诊断：`absent_everywhere`；两项覆盖均为 0.00。

- 原文 ID：`src:346e4991ddda49115f916fdcb89271cc`，`2023-11-16T07:59:00+00:00`，assistant：“25 minutes on, then 5 minutes off for something fun”，上下文明确是在分段学习。
- 派生事实：`fact:490c30b7ebb93eeb6020e4e9fcc5cfb4`，明确是 Tim 的 studying，保留 25 分钟学习/5 分钟休息。
- B 最终证据包含该事实。
- 支持判断：这是需要以一般知识把描述归类为 Pomodoro 的推理题。证据描述足够，答案名称不必在源文本中逐字出现；词面 0 分无法证明数据无支持。此处不主张派生库应该预先写入金标名称，更不允许使用测试金标生成记忆。

### 4. 单跳、并列答案：conv-48#q0055

问题：What do Deborah and her husband do together? 金标：play detective games together, spend time outdoors and explore nature。

原诊断：`absent_everywhere`；两项覆盖均为 0.44。

- 原文 `src:6511dee8a67956c1f123cacfe95d19f0` 明确引出与丈夫一起玩游戏；同观察时间 `2023-08-18T16:52:00+00:00`。
- 原文 `src:17c15a0e99351dd6807610643a0f4a59`：“We prefer to play detective games together.”
- 原文 `src:9111c9ad0721b3443bd413e4ae941649`：“We also enjoyed spending time outdoors and exploring nature.”
- 派生事实 `fact:4e7bd64d04996226bf4853620f6896e9` 保留侦探游戏；`fact:4703b672a9d860df75cd7721804cedd8` 保留户外和自然；`fact:895ed26db2f52dd37547e23a87ebef0c` 保留关于 user 与 husband 共同活动的中间提问。
- B 最终证据均包含上述派生支持。
- 支持判断：整组上下文覆盖并列答案；任何单句覆盖不足 60% 不能被解释成“原文不存在”。数据集类别为单跳也不等于只允许一条原文支持。

## 指标定义中需要改正的边界

1. 目前 `in_facts` 只检查整个会话事实库，遍历 `store['facts']`，没有读 B 的请求、top-400 候选、24 个窗口或最终渲染。因此它是全库词面覆盖，不是召回率。
2. `in_facts` 也不保证支持关系：同会话中错误人物、否定句、被取消计划、另一个事件，只要金标词重合就可能过线。
3. `absent_everywhere` 应改名 `single_text_gold_overlap_below_threshold`，保留为调试特征。不能当作“证据不存在”的最终标签。
4. 多条原文/多条派生事实的组合支持，以及观察时间加相对时间的计算，是当前最大值单条匹配完全不衡量的维度。
5. `CS:GO` 题 `conv-47#q0058` 因分词后的 `cs`、`go` 均不超过 2 个字符，被当作 `empty_gold_tokens` 丢弃。下一轮语义审计应保留全部 80 题，不得因当前分词器无法编码答案就从覆盖分母中删除。
6. 0 个 lost_in_summary 不能否定 R2 价值，也不能证明 R2 值得全量投入。需要先用可复核的语义支持审计决定：原文支持但派生事实缺失/错绑才是摘要损失；原文与派生均支持而 B 最终证据缺失才是召回/选取损失。

## 建议的下一轮诊断合同

先固定原来的 80 题作为开发诊断集，逐题保存下列独立判断与支持 IDs，不改变任何测试金标或运行时检索：

| 阶段 | 判断对象 | 输出 |
|---|---|---|
| S0 | 冻结原文及必要相邻上下文 | 完整/部分/无支持/需外部常识；source IDs、人物与时间锚 |
| S1 | 整个派生事实库 | 完整/部分/无支持；fact IDs、是否丢角色/指代/事件时间 |
| S2 | B top-400 命中及其窗口扩展并集 | 是否包含支持集；支持中心的排名与是否仅上下文可达 |
| S3 | B 最终渲染的证据字符串 | 支持集是否真实呈现，日期/标注/说话人是否仍可读 |
| S4 | 每个高层候选及其回溯后的具体事实 | 是否补充 B 未呈现的支持或改善排序；不能以全库已存在为拒绝理由 |
| S5 | 已有答题/判分（如有） | 有支持仍答错、判分波动、或无支持答对，分别记录 |

语义判断由盲于原分数和路由组别的审核过程产生；允许金标用于离线诊断和人工核验，严禁写入候选生成、记忆、索引或运行时选择提示。人工支持集可以作为之后的覆盖评估标签，不能当作实时检索输入。

对时间题单列“事件绑定/观察锚/相对短语/粒度/可计算结果”；对推理题区分“证据描述+允许的一般常识”和“数据根本不支持”。只有 S0 完整而 S1 不完整的样本才能支持 R2 摘要保真改造的优先级；S1 完整而 S2/S3 失败应先修召回、候选或渲染；S3 完整而答案错应诊断答题和时间推理。

高层与低层同源是设计特征，不能据此断言高层无用。高层目标可以是让同一事实库中的支持事实更容易进入受预算限制的最终证据；其增益单位应是“相对于本题最终 B 的支持覆盖/排序/成本变化”，而不是要求发现低层全库不存在的事实。也不建议只索引“低层全库未覆盖的事实”，因为路由正常就应指向已存储的低层事实。

