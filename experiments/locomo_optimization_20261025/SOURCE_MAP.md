# 源代码与依赖地图

该清单按本地实际文件及静态 import 核对。`source_inventory.json` 列出每个文件的源位置、建议包内位置、阶段、大小、SHA-256 与导入。它不把全部历史代码都当成当前已采用方案。

## 最终执行链

1. `full_prepare.py`：读取861题、统一事实库、折叠路由，构造 B/L/H 和完整筛选提示。
2. `r13b_prepare.py::build_pools()`：L=12低层；H=8低层+最多4高层，缺额低层回填。该文件中仍保留历史有缺陷的 `apply_plan()`，最终执行器不调用它。
3. `full_run.py` → `screen_client.py`（R1.2目录）调用筛选模型 → `r13b_fix.py::apply_plan_fixed()` 固定标签交换 → `grounded.Retriever.render()`。
4. `grounded.py` → `upper_w0.py` → `build_hierarchy.py`、`run_full.py`、`cloud_pipeline.py`，以及 W0 `arms/arm_w0.py`、`arm_b.py`、`week_ext.py`；`arm_b`在导入时动态加载 `frozen/a0_temporal_evidence.py`。即使只跑最终检索，也不能遗漏这些 helper。
5. `full_answer.py` → bench `runners/aml_run.py` → `evaluators/aml_score.py`；此处包含真正的答题、判分提示，及完整请求缓存身份。bench 的静态本地导入闭包另放 `source/bench/`。
6. `repair_cached_answers.py`：仅定向修正两道真实缓存冲突；`finalize_results.py`：已有结果离线汇总；`test_scoring_cache.py`：三项回归。

## 最小运行输入与重建输入

| 输入 | 用途 | 大小 |
|---|---|---:|
| `locomo_temporal_week_20261023/frozen/questions.json` | final_frozen_question_set | 283853 |
| `locomo_hierarchy_grounded_20261024/store.json` | final_fact_bank | 24149049 |
| `locomo_hierarchy_grounded_20261024/index.sqlite` | final_retrieval_index | 7303168 |
| `locomo_hierarchy_relevance_r1_20261024/routes_r1.json` | final_folded_route_index | 6666553 |
| `locomo_upper_w0_20261024/memories.json` | rebuild_upper_annotations | 16398498 |
| `locomo_space_l1_l5_20261024/generated_memories.json` | rebuild_original_hierarchy | 6680035 |
| `locomo_space_l1_l5_20261024/hierarchy_plan.json` | rebuild_hierarchy_plan | 1722066 |
| `locomo_temporal_v2_20261023/frozen/aml_sources.sqlite` | rebuild_raw_source_snapshot | 4313088 |

最终 prepare/retrieval 只需题单、store、index、routes这四项核心输入；从零重建事实库和注释时才需原层级、plan、upper memories与原始source快照。实际答题/判分还需外部模型配置，打包时应提供占位模板，不能复制本机密钥设置。

## 前期原文与时间优化

- `locomo_full_20261022/runtime_fix.diff`：异步出口归属检查移出事件循环；`app.before.py`仅作旧版本对照。
- `locomo_smoke300_20261022/source_evidence.py`、`test_source_evidence.py`、`test_source_api.py`、`app.staged.py`、`app.diff`：原文FTS、稳定来源、上下文窗口、来源去重与 API 接线补丁。
- `server/source_evidence.py`、`server/temporal_evidence.py`：AML源码副本模块；当前`server/app.py`没有调用这两个模块，不能宣称生产默认启用。
- `locomo_temporal_v2_20261023/patch/`：A0 时间解释、source v2、差异补丁与测试。
- `locomo_temporal_annotation_full_20261023/arms/`与`frozen/`：C1采用路线和C2/C3对照；重复冻结副本保留以核对历史输入。
- `locomo_temporal_week_20261023/arms/`与`frozen/`：W0采用；W1精确周区间为未采用分支。

## 历史或未采用分支

- R1 `r1_pipeline.py`、`r1_eval.py`：曾有目标窗口/替换缺陷，不能作为最终执行器。
- R1.1 `p1_*`：粗槽位、过强尾部保护、初版模型筛选；属于排查历史。
- R1.2 `support_atoms.py`、`controls.py`与审计脚本：支持原子尝试和候选评估；只有`screen_client.py`仍是最终依赖。
- R1.3 `stage_b.py`：原联合尾部重排仍有截断和纯删问题，未用于最终全量。
- R1.3b `r13b_run.py`、`r13b_answer.py`：旧20题运行器和旧缓存，不应替代`full_*`；`r13b_fix.py`和`r13b_prepare.py`的指定函数为最终有效依赖。
- `configure_*`、`probe_*`、`inspect_*`、`switch_model.py`等属于环境维护/云端探索；如随包留档，放历史目录并勿作为默认复现入口。
- `cache_repair_20261025/before/full_answer.py`是缓存修复前对照，只作审计。

## 路径与上游集成边界

这是保持原样的源码快照；许多脚本含本机绝对路径，bench配置依赖原项目环境。复制到桌面不等于已改造成即装即跑的项目包。提交上游时应将路径参数化、按上游模块布局迁移，并在现有TiMEM/AML仓库内运行 API 接线测试；不能把原仓库整套核心依赖误计为本次新增。

## 盘点结果

共 158 个代码/补丁/依赖文件；疑似凭据模式命中 0 个文件。扫描只记录命中与路径，不输出密钥。未纳入本机 `.env`、Space 账户配置、API设置数据库、凭据文件。
