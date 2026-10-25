# 提示词与证据格式附件

本目录从本地实际源代码用 AST 精确导出，不导入项目模块、不运行模型、不请求网络。源文件 SHA-256、符号、行号与导出文件 SHA-256 见 `PROMPT_SOURCE_MANIFEST.json`。`export_prompts.py` 是导出工具，不是评测入口。

“当前”指 2026-10-25 全量方案及两题缓存修正使用的代码路径；“历史”表示用于解释演进，不能与当前模板混用。“动态证据格式”是拼入 memories 的正文，不是另外发给模型的一条 system prompt。

## 当前全量方案

| 附件 | 用途与来源 |
|---|---|
| `01_CURRENT_answer.txt` | 实际答题模板 `_OPEN_ANSWER`，保留 `{memories}`、`{question}` 占位符 |
| `02_CURRENT_judge.txt` | 实际判分模板 `_ACCURACY`，保留问题、金标和生成答案占位符 |
| `03_CURRENT_answer_prompt.py`、`03_CURRENT_judge_prompt.py` | 数据集分支与填充方式；LoCoMo 走普通开放问答分支 |
| `03_CURRENT_format_memories.py` | 主证据格式和预算；不得把注释字符预算当成全部证据预算 |
| `03_CURRENT_complete_chat.py` | 答题/判分请求协议；函数只有参数引用，没有凭据值 |
| `04_CURRENT_joint_rerank.txt` | 全量 `full_prepare.py::PROMPT_TEMPLATE` 完整模板 |
| `04_CURRENT_build_prompt.py` | 用完整 JSON 填入 protected、tail、optional 的实际方法 |
| `15_CURRENT_compositional_guard.py` | 是否运行联合重排的实际规则；不读金标或类别 |

提示词源码仍含原有英文措辞和格式，包括判分模板中先要求短解释、随后要求只返回 JSON 的原文。此附件忠实保留实际模板，没有为 PR 美化或悄悄更换判分标准。

## 历史构建和筛选模板

| 附件 | 历史用途 |
|---|---|
| `05_HISTORICAL_hierarchy_prompt_for.py` | 五层记忆构建函数原文，含 L1 至 L5 的不同 purpose 和 child 数据拼装方式 |
| `05_HISTORICAL_hierarchy_fixed_template_source.txt` | 同一函数的 f-string 源码表达式；不是已填充的模型请求，实际内容还依赖节点及子记忆 |
| `05_HISTORICAL_generation_protocol.py` | 构建时的层序、单子节点直接保留、模型调用协议及 prompt hash 记录 |
| `06_HISTORICAL_R11_screen.txt` | R1.1 候选逐条审查模板 |
| `07_HISTORICAL_R12_screen.txt` | R1.2 direct/bridge/context 等候选判定模板 |
| `08_HISTORICAL_R13_joint.txt` | R1.3 初始联合重排模板 |
| `09_HISTORICAL_R13b_joint.txt` | R1.3b 联合模板；历史执行器中的截断问题不能凭模板完整性排除 |

以上历史模板不是新增的当前调用步骤。当前全量调用的是 `04_CURRENT_joint_rerank.txt`；候选池及换入换出的执行以全量源代码为准。

## C0–C3 与 W0/W1：改变的是证据正文

| 分支 | 实际差异 | 附件 |
|---|---|---|
| C0 | V1 原文窗口，无日历注释；没有独立“C0 答题提示词” | 原文 evidence 组装见配套源代码 |
| C1 | A0 短格式，raw_phrase、observed_at、calendar_value、precision、rule、source_id | `10_DYNAMIC_C1_*.py` |
| C2 | 同一 A0 解析结果，展示 span、anchor_date、status 等字段 | `11_DYNAMIC_C2_*.py` |
| C3 | 除格式外还改变复杂短语屏蔽、去重、原句关联等；不能只归为加字段 | `12_DYNAMIC_C3_*.py` |
| W0 | 在 C1 上给简单 last week/last weekend 加原句锚点，with_range=False | `13_DYNAMIC_W0_annotate.py`、`13_DYNAMIC_WEEK_*.py` |
| W1 | 加日历区间和周首约定，with_range=True；性能分支未作为最终主方案采用 | `14_DYNAMIC_W1_annotate.py`、`13_DYNAMIC_WEEK_*.py` |

动态片段为函数原文，依赖相应模块中的数据类型、正则和解析函数，不是可以脱离源代码独立运行的插件。C1/C2/C3 的注释开头是 `Calendar annotations derived from the original turns:`，源函数保证原时间短语和来源关系；W0 保留周粒度，不凭空指定一天。最终五层事实渲染沿用 C1/W0 风格，但建设时绑定到事实，答题检索不再读取 L0 原文。

完整运行约束见 `PROMPT_PROTOCOL.md`。设计与验证材料清单见 `DESIGN_DOCUMENTS_INDEX.md`。
