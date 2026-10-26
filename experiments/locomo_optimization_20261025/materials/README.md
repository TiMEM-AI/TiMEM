# PR 成果材料入口

最终结果已完成两题缓存修正：**B/L 723/861（83.97%），H 728/861（84.55%），净 +5 题。**

## 推荐阅读顺序

1. `PR_DESCRIPTION.md`：可用于 GitHub PR 描述的精简正文。
2. `OPTIMIZATION_REPORT.md`：从接手基础到当前方案的 17 项具体改动、真实前后实例、历史实验与边界。
3. `FINAL_VERIFICATION.md`：两题补评及最终分数的独立复核。

## 附件

- `FINAL_RESULTS.json`、`RESULTS_FINAL.csv`、`RESULTS_TABLE.md`：同一份修正后成绩的不同格式。
- `EXAMPLES.json`：8 个真实来源、历史对照或本轮补评案例；已区分所属实验。
- `HISTORY_BEFORE_HIERARCHY.md`、`HIERARCHY_CHANGE_CATALOG.md`：详细事实与代码落点。
- `REPOSITORY_CONTEXT.md`：原有能力、仓库规范及产品集成范围。
- `SCORING_CACHE_FIX.patch`：本次评分缓存修复及定向补评/汇总/回归脚本的实际差异，供审阅与迁移；尚未应用到上游仓库。
- `RELEASE_MANIFEST.json`：文档、代码、最终结果及关键输入的 SHA-256。

## 放入项目仓库时

已确认上游项目为 TiMEM-AI/TiMEM。建议把完整报告存为 `experiments/results/locomo_optimization_20261025.md`，随附最终结果、实例和协议 hash。PR 正文可使用 `PR_DESCRIPTION.md`。

五层优化主体目前仍是本地实验参考实现，运行脚本依赖历史实验模块和本机路径。若一并提交代码，需要提炼最终调用链、改成可配置路径；不要把本文档当作已完成生产 API 集成的证明。

当前没有创建、推送或发布 GitHub PR，也没有修改上游源码树。这里提供可审阅的提交材料。

## 本地验证命令

在 `locomo_hierarchy_relevance_full_20261025` 目录，用现有评测 Python 环境执行：

```powershell
python -B -m unittest test_scoring_cache -v
python -B repair_cached_answers.py
python -B finalize_results.py
```

以上均不重新调用模型：第二条默认只检查碰撞和请求计数，第三条仅从已有答案汇总。`repair_cached_answers.py --execute` 只用于授权的补评；本次已存在完成标记，再次执行会跳过模型调用。不要用 `full_answer.py` 代替定向修复，它会重新评测全部题目。

真实两题补评档案保留在 `locomo_hierarchy_relevance_full_20261025/cache_repair_20261025/`。旧报告与旧答案在 `before/`，请求/判分原文在 `requests/`、`results/`、`model_calls.jsonl`。PR 附件未包含认证密钥或环境配置。
