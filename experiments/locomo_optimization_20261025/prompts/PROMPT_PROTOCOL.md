# 提示词协议与复现边界

## 1. 当前答题与判分

来源：`F:/worktable/research/bench/evaluators/aml_score.py`，实际入口：`locomo_hierarchy_relevance_full_20261025/full_answer.py`。

- LoCoMo 答题使用 `_OPEN_ANSWER.format(memories=..., question=...)`。
- 判分使用 `_ACCURACY.format(question=..., gold_answer=..., generated_answer=...)`。金标仅进入判分，不进入记忆构建、检索或联合重排。
- 两者消息协议均是 `messages=[{"role":"user","content":prompt}]`，temperature=0。模板没有另外一条隐藏 system 消息。
- 当前报告答题和判分模型均为 `deepseek-v4.1-flash`；模型名是实际运行记录，不代表提示词自身固定模型。
- 评测器主证据输入预算为 117,760 tokens 减问题及选项开销；时间注释另有 8,000 字符预算。不可把该字符预算理解为全部证据上限。
- 两题修正后的缓存 identity 包含问题/金标/类别、完整答题请求、模型与接口、判分模板等，认证值不入 public identity。同题 B/L/H 的相同证据可复用；不同问题即使证据相同也不可复用。

## 2. 当前联合重排

来源：`full_prepare.py` 与 `full_run.py`；底层屏蔽客户端来自 R1.2 的 `screen_client.py::screen_raw`，不使用它的旧候选审查 PROMPT。

- B 是受控的 L1/L2 事实检索基线。
- L 提供最多 12 个额外低层候选；H 提供最多 8 个低层与 4 个高层导航候选，具体不足时以实际池为准。
- protected 是前 20 个窗口；tail 是其后最多 4 个；最多总共 24 个窗口。
- 用完整 JSON 展示事实、日期和编号。候选不是只给标题，也没有只展示一段 160/420 字符摘要。
- 模型最多选 4 个 optional，并删除同样数量 tail。未知编号、重复编号、受保护项删除、纯删除、数量不匹配、选中 constraint_mismatch 等由代码拒绝。
- 模型输出 relevance、supporting_fact_ids、query_relation、answer_value_or_needed_link 等字段是审查信息；真正强制执行的条件以 `full_run.py::validate` 为准，不能把所有自报字段都说成硬验证。
- 24,000 的筛选 budget 在执行器中按约 4 字符/token 估计；超限删除完整 optional 后重建完整模板，不切断 JSON 正文。它并非 token 编码器精确计数。
- `MAX_TRIES=2` 表示最多两次调用尝试。失败显式回退 B。
- 861 题中 454 题 guard 跳过，407 题每组计划运行筛选。guard 是程序规则，不按金标或题型标签绕过。

## 3. 历史五层构建

`build_hierarchy.py::prompt_for` 用同一固定要求配合不同 purpose：L1 提取对话段，L2 合并一次会话，L3 合并一天，L4 合并一周，L5 合并月度画像/事件时间线。DATA 的动态内容来自原始段或已有子节点；仅有一个子节点时直接保留，不额外调用模型。

层级构建要求保留具体名字、地点、数量、状态、原时间短语和各事实自己的 observation date，不推断无依据的人名。它是历史实际构建方式；后来的 grounded 改造使用统一低层事实库和高层路径，不是每轮又调用此模板生成五遍摘要。

## 4. C/W 分支与当前模板的边界

C0–C3/W0/W1 不是七套答题模型提示词，而是历史证据后处理方案。它们把不同的动态注释拼入 memories 后再交给共享 AML 答题评测器。字段格式、解析规则、去重、预算和路由必须一同追溯，不能把所有差异都归为提示词措辞变化。

本包导出的是实际源码和实际常量，未拼装虚构的具体模型请求，也未从问题金标反向构造演示模板。逐题请求的重建需要同一问题、事实库、路径索引、候选池和源代码；单独拷贝提示词无法复现成绩。

## 5. 版本核对

全量 `RUN_MANIFEST.json` 的 `prompt_template_sha` 为：

`7a0707c4306345f1987bbd6d8cb526be6d2a8fdb56d7081f39a447ab9cc98fce`

这里对应模板字符串值（UTF-8）的 SHA-256。源码文件有 CRLF 等字节差异时，整个文件的 hash 可以与规范化文本 hash 不同；本目录 `PROMPT_SOURCE_MANIFEST.json` 记录实际源文件字节 hash 及导出文件字节 hash，二者用途不同。
