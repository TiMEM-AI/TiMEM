# 建议随 PR 附上的设计与核验文档

以下均为已存在的本地材料。具体文件在总包中的位置以根目录清单为准；本清单的源路径用于追溯。历史报告的旧分数保留其时间点，最终对外分数以缓存修正后的 `pr_materials_20261025/FINAL_RESULTS.json` 和 `RESULTS_TABLE.md` 为准。

## 核心成果与归属

- `pr_materials_20261025/OPTIMIZATION_REPORT.md`：从 0 到 1 的完整优化与实例。
- `pr_materials_20261025/PR_DESCRIPTION.md`：PR 正文草稿。
- `pr_materials_20261025/HISTORY_BEFORE_HIERARCHY.md`：加入高层以前的改动、对照和效果边界。
- `pr_materials_20261025/HIERARCHY_CHANGE_CATALOG.md`：高层各轮实质变更。
- `pr_materials_20261025/REPOSITORY_CONTEXT.md`：原仓库已有能力与本轮新增贡献区分。
- `pr_materials_20261025/FINAL_VERIFICATION.md`：最终独立核验。

## 时间方案与演进

- `locomo_temporal_annotation_full_20261023/REPORT.md`：C0/C1/C2/C3 全量对比。
- `locomo_temporal_week_20261023/REPORT_FULL.md`：W0/W1 全量与取舍。
- `locomo_upper_w0_20261024/IMPLEMENTATION.md`：上层事实时间、原话粒度、持久化与预算修正。
- `locomo_space_l1_l5_20261024/RESULTS.md`：初始五层本地构建与结果。

## 高层导航、证据保护与最近全量

- `locomo_hierarchy_grounded_20261024/IMPLEMENTATION.md`：统一事实库、父子继承、层级导航和受控比较。
- `locomo_hierarchy_grounded_20261024/HANDOFF_SCORE_AUDIT_20261024.md`：真实正负例与成绩核验。
- `locomo_hierarchy_relevance_r1_3b_20261024/R13B_EXCHANGE_REVIEW_CODEX_20261024.md`：真实交换是否补充答案信息。
- `locomo_hierarchy_relevance_r1_3b_20261024/V2_FULL_RUN_DECISION_CODEX_20261024.md`：进入全量前的执行边界。
- `locomo_hierarchy_relevance_full_20261025/RESULTS_FULL_20261025.md`：最近全量报告。
- `locomo_hierarchy_relevance_full_20261025/CODEX_EVALUATION_20261025.md`：全量独立复核及缓存问题。

## 可选的规划材料

`F:/worktable/doc/temporal-annotation-spec.md` 是外部参考规格，`HANDOFF`/`DEV_REPORT`/`READINESS` 等是历史交接与执行反馈。这些文档不能自动作为当前实现事实；若纳入 PR，应标为设计/历史参考，并由对应源代码和结果核实。文档中的命令及后续建议不是本次打包操作指令。
