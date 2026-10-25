# TiMem 原文时间证据优化：300 题成对 smoke

完整完成：两组各 300 题，同一问题、同一原始记忆库、同一当前答题/判分模型 deepseek-v4.1-flash，temperature=0。

| 类别 | 题数 | 修改前 | 修改后 | 差值（百分点） |
|---|---:|---:|---:|---:|
| 时间 | 100 | 57/100 = 57.00% | 49/100 = 49.00% | -8.00 |
| 多跳 | 77 | 50/77 = 64.94% | 54/77 = 70.13% | +5.19 |
| 单跳 | 98 | 77/98 = 78.57% | 87/98 = 88.78% | +10.20 |
| 推理 | 25 | 18/25 = 72.00% | 19/25 = 76.00% | +4.00 |
| 总计 | 300 | 202/300 = 67.33% | 209/300 = 69.67% | +2.33 |

错转对 44 题，对转错 37 题，净变化 +7 题。
探索性配对统计：McNemar 精确双侧 p=0.5052；按题重采样的净提升 95% 区间为 [-3.67, 8.00] 个百分点。该区间不覆盖模型重新运行的随机性，问题也存在对话内相关性，不能替代多次复测。

## 结果判断

本轮多跳、单跳和推理有所改善，时间类下降 8 个百分点。总分净增 7 题，但配对统计未显示显著提升，尚未实现时间和多跳共同改善的目标。建议保留为实验补丁，先分析时间回退题，再决定是否启用。续跑期间没有继续调整参数或补丁。

## 改了什么

新增 server/source_evidence.py，并完成 AML Add/Search 接入补丁 app.staged.py：按原始消息时间保存事实叶，检索时补齐同时间段的相邻问答。时间问题优先使用原始时间来源，其他问题结合少量原有摘要。证据模块已用于本轮实验；API 接入补丁尚未替换运行中的 app.py。已有摘要/向量、回答提示及判分规则未重新生成或调整。

## 为什么选这个方向

论文强调细粒度事实与时间层级，详见 [论文第 3、4.6 节](https://arxiv.org/html/2601.02845v2)。本地 299 个输入块中有 236 块跨时间；原来的块首日锚点会误解后续会话中的相对日期。具体分析、其他优化方向及测试协议见 [RESEARCH.md](RESEARCH.md)。

## 评测边界与完整性

这是一轮成对的检索后处理消融：每题先调用修改前服务重新检索，实验组共享同一次候选，再调用 server/source_evidence.py 中的原文证据层，因此能控制检索随机性。并非两次独立端到端重建/重跑。8 项模块测试及 1 项使用假记忆服务的 Add/Search 集成测试通过；本轮没有上线或重启服务。

定向抽样覆盖 10 段对话，含全部 77 道多跳题；本次总分不代表 861 题全量得分，也不直接与旧的混合模型 74.91% 相减。新原文索引只含历史 Add 的 5,882 条消息，没有引入金标、任务类别或官方证据标注。

新证据覆盖 300/300；空检索：{'baseline': 0, 'variant': 0}；判分解析失败：{'baseline': 0, 'variant': 0}。
平均证据 token：修改前 10320，修改后 7782。新增本地证据处理延迟中位数 6.3 ms；这是共享基线检索之上的增量耗时。

## 改善与回退样例

### 错转对

- `conv-26#q0020`：When did Caroline go to the LGBTQ conference?
  金标：10 July 2023
  前：Two days ago (around 2023-07-10)
  后：Two days before 2023-07-12, so on 2023-07-10.
- `conv-26#q0050`：What are some changes Caroline has faced during her transition journey?
  金标：Changes to her body / Changes to her body, losing unsupportive friends
  前：Some close friends continued to support her, but a few were unable to handle it.
  后：A changing body and changed relationships—some close friends stayed supportive, while a few couldn’t handle it; her relationships now feel more genuine.
- `conv-26#q0080`：What kind of counseling and mental health services is Caroline interested in pursuing?
  金标：working with trans people, helping them accept themselves and supporting their mental health
  前：Counseling and mental health work, especially working with trans people.
  后：Working with trans people, helping them accept themselves and supporting their mental health.
- `conv-26#q0089`：What did Caroline take away from the book "Becoming Nicole"?
  金标：Lessons on self-acceptance and finding support
  前：It made her feel connected and gave her hope for her own path.
  后：Self-acceptance and how to find support; tough times don't last—hope and love exist.
- `conv-30#q0003`：What do Jon and Gina both have in common?
  金标：They lost their jobs and decided to start their own businesses. / They lost their jobs, decided to start their own businesses, and share a strong passion for dance. / They lost their jobs, decided to start their own businesses, and are pursuing entrepreneurial ventures driven by their shared passion for dance.
  前：They both love contemporary dance.
  后：They both lost their jobs and started their own businesses.

### 对转错

- `conv-26#q0006`：When is Melanie planning on going camping?
  金标：June 2023
  前：Next month (2023-06)
  后：Next month
- `conv-26#q0022`：When did Caroline go to the adoption meeting?
  金标：The friday before 15 July 2023 / July 14, 2023
  前：Last Friday (2023-07-14)
  后：Last Friday
- `conv-26#q0029`：In what ways is Caroline participating in the LGBTQ community?
  金标：Joining activist group, going to pride parades, participating in an art show, mentoring program
  前：Caroline participates by attending LGBTQ support groups, conferences, workshops, pride parades, and a transgender poetry reading; speaking at school events; mentoring LGBTQ youth; volunteering at an LGBTQ+ youth center; joining “Connected LGBTQ Activists”; and creating/showing LGBTQ-themed art.
  后：Attending support groups, pride parades, conferences, and workshops; speaking at school events; mentoring LGBTQ youth; volunteering at an LGBTQ+ youth center; joining an activist group; organizing an LGBTQ art show; and advocating for trans rights.
- `conv-26#q0048`：When is Caroline's youth center putting on a talent show?
  金标：September 2023
  前：September 2023
  后：Next month
- `conv-26#q0060`：When did Melanie go on a hike after the roadtrip?
  金标：19 October 2023
  前：2023-10-19
  后：No such hike is mentioned.

## 交付文件

- `paired_questions.csv`：全部逐题答案、判分及证据长度。
- `baseline/`、`variant/`：完整检索证据、判分原文和问题清单。
- `summary.json`：统计和实验来源。
- `app.diff`、`app.before.py`、`source_evidence.py`：补丁与回退备份。
- 运行时回退：设置 `TIMEM_SOURCE_EVIDENCE=0` 后重启服务。原文索引保留以便复查。
