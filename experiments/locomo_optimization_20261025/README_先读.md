# TiMEM 优化成果与 PR 完整附件

日期：2026-10-25。最终结果：**B/L 723/861（83.97%），H 728/861（84.55%），净 +5 题。** 已包括两题跨问题缓存修正。

## 先看这三个文件

1. `OPTIMIZATION_REPORT.md`：17 项优化、实质修改、真实前后案例、各轮成绩与采用/放弃的方案。
2. `PR_DESCRIPTION.md`：可用于 GitHub PR 正文的精简版本。
3. `SOURCE_MAP.md`：每项改动的源码位置、最终调用链和历史分支说明。

## 文件夹内容

| 目录/文件 | 内容 |
|---|---|
| `source/experiments/` | 各轮实际改动的 Python、PowerShell、补丁和测试；保留原实验目录层次及历史对照 |
| `source/timem_aml/` | 原文证据/时间模块、API 接口参考源码及相关测试；不代表已整体接入生产 |
| `source/bench/` | 最终评测依赖、答题/判分实现、配置占位示例、依赖及许可文件；参考依赖不全算本轮新增 |
| `prompts/` | 实际答题、判分、联合重排、五层生成、历史筛选及动态时间格式；28 项精确提取及来源 hash |
| `docs/experiment_history/` | 各轮报告、交接、修复审阅和实施规划 |
| `docs/design/` | 最初交接及时间标注规格等设计参考 |
| `materials/` | 成果整理、独立复核、代码差异和原始材料清单 |
| `results/` | 修正后的 JSON/CSV、最终报告及历史汇总/协议/审计 |
| `audit/` | 两题补评的原始请求、响应、旧答案、测试记录、复制及压缩附件索引 |
| `attachments/` | 完整逐题产物和冻结数据压缩附件，详见下节 |
| `source_inventory.json` | 158 项改动/依赖的来源、包内位置与 hash；另有补充参考源码 |
| `BUNDLE_MANIFEST.json` | 本包每个文件的大小与 SHA-256 |
| `tools/verify_bundle.py` | 纯离线包完整性与压缩附件校验工具 |

## 两个完整数据附件

- `attachments/full_run_evidence_20261025.zip`：最终 861 题三臂答案、2,583 份证据、1,722 份完整筛选请求，以及状态、响应、任务和协议记录。保留完整证据用于追踪实际选入内容。
- `attachments/frozen_memory_inputs_20261025.zip`：原五层快照、上层时间存储、7,157 条统一事实库、SQLite 索引、折叠路由、冻结题单、历史候选和原始来源数据库。

压缩包成员路径以 `source/experiments/` 开头。若需查看完整数据，可解压到本材料根目录，补齐同名实验源码目录中的输入和产物。它们是复核附件，不是将云端账户数据或本机密钥一并导出。

## 提 PR 时的范围

这是原样源码与成果附件集合，包含已采用及未采用的历史分支。默认审阅入口是 `locomo_hierarchy_relevance_full_20261025/full_*`；不要把旧 `r13b_run.py`、旧 `apply_plan()` 或修复前缓存当成最终实现。具体边界见 `SOURCE_MAP.md`。

完整报告可放入仓库 `experiments/results/locomo_optimization_20261025.md`，附最终成绩、案例、协议和所选代码。历史全量数据集中压缩提供，便于按 PR 范围选取附件。包中没有创建、推送或发布 PR 的操作。

原脚本保留了实际实验的绝对路径和历史依赖；不能把复制材料等同于已完成生产集成或换机器即运行。迁入上游仓库时应按 `PORTABILITY.md` 调整路径和模块布局。没有为整理材料而悄悄修改算法或重跑模型。

## 检查材料是否完整

在此目录使用 Python 运行：

```powershell
python tools/verify_bundle.py
python tools/verify_bundle.py --archives
```

第一条校验全部文件 hash；第二条还逐项核验压缩包内的文件。两者均不联网、不调用模型。模型密钥需自行从环境加载，包内仅提供空白示例。

旧报告可能保留历史分数。最终对外数字以 `results/FINAL_RESULTS.json`、`results/RESULTS_FINAL.csv` 和根目录完整报告为准。

最初交接文档副本中的一处本机 API 凭据已替换为占位符，原文件未改；脱敏范围见 `audit/REDACTIONS.json`。源码和提示词按实际版本保留。
