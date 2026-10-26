# GitHub PR 仓库上下文与提交范围建议

核验日期：2026-10-25。本文基于本地文件、Git 元数据和仓库内贡献规范；未访问网络、未修改这些源码目录、未创建或发布 PR。本文不是正式产品集成完成证明。

## 1. 已确认的仓库身份

| 本地目录 | Git 状态与来源 | 可以得出的结论 |
|---|---|---|
| `F:/worktable/research/timem` | 存在 `.git`；分支 `main`；HEAD `6d279a5`，提交标题为 `Add roadmap: memory architecture, inference efficiency, multi-agent ops`；origin 为 `https://github.com/TiMEM-AI/TiMEM.git` | 可确认为 TiMEM-AI/TiMEM 的本地源码树和 Git 元数据。 |
| `F:/worktable/research/timem-aml` | 当前目录没有 `.git`，`git status` 报 `not a git repository` | 是含 AML/server/frontend 等扩展的本地源码副本，不能把其中现有文件直接说成已纳入上游 Git 提交。 |
| `F:/worktable/research/bench` | 当前目录没有 `.git`，`git status` 报 `not a git repository` | 是评测工程副本。README 的 CI 徽章指向 `JoanhLan/agent-memory-benchmark`，但没有本地 remote 佐证，不能据此确认本次 PR 的目标仓库。 |

补充：在 `timem` 中，`git branch`、`git log`、`git ls-files` 和 `rev-parse` 可以读取；`git status` 与工作树 diff 多次返回 `this operation must be run in a work tree`，即便显式提供绝对 `--git-dir` 与 `--work-tree` 仍如此。`core.bare` 为 false，`rev-parse` 一度报告处于工作树。故本次**不能报告该仓库工作区干净或给出可信的完整工作树变更清单**；没有修改 Git 配置尝试修复。后续真正整理提交时，应在可正常执行状态检查的 checkout 中完成。

## 2. 原系统已经有的能力，应与本项目改动分开

TiMEM 原仓库已经存在五层记忆架构：

- `timem/memory/l1_fragment_memory.py`
- `timem/memory/l2_session_memory.py`
- `timem/memory/l3_daily_memory.py`
- `timem/memory/l4_weekly_memory.py`
- `timem/memory/l5_high_level_memory.py`

原 `experiments/README.md` 也已描述 LoCoMo 三步流程，以及从对话生成 L1–L5。因而 PR 宜写“在已有五层架构上实现和验证时间锚定、事实继承、高层导航与受约束证据选择”，不宜写成“首次为 TiMEM 发明或加入 L3–L5”。

原实验 README 中的 75.30% 等数字属于其原有说明。本轮 861 题离线评测与那些数字的样本、模型、评分协议未作一致性核验，不宜直接把两者相减作为本次改动的收益。

## 3. 当前实现实际位于哪里

### 3.1 本地 AML 源码副本里已有的文件

`F:/worktable/research/timem-aml/server/source_evidence.py`：

- Add 来源的逐轮、带观察时间的 SQLite/FTS5 索引。
- 查询侧原始证据检索及时间渲染开关。
- 文件说明明确只接收 Add 内容，不从答案或标签生成检索索引。

`F:/worktable/research/timem-aml/server/temporal_evidence.py`：

- 保守的日期问题识别。
- 原话中的相对日/月/年转换与独立注释渲染。
- 当前文件头说明 week/weekend 等不在这个 A0 模块的支持范围内；后续 W0 周标注在实验代码中。

`F:/worktable/research/timem-aml/tests/test_temporal_evidence.py`：对应 A0 的单元测试文件。

**集成界限：** 本次只读搜索在当前 `server/*.py` 和 `shim.py` 中未发现外部调用 `source_evidence` 的入口，引用主要位于该模块自身。因此这里只确认模块与测试文件存在，不能据此声称当前产品 API 已实际接入全部优化。入口和部署版本需要在产品集成 PR 中另行核对。

### 3.2 主要五层优化仍在实验目录

| 实验目录 | 可定位的实现 | 主要作用 |
|---|---|---|
| `locomo_space_l1_l5_20261024` | `build_hierarchy.py`、`run_full.py` | 从冻结来源生成可复现五层快照、最初离线测试。 |
| `locomo_upper_w0_20261024` | `upper_w0.py` | L3–L5 的逐事实观察时间、覆盖窗口分离、原话时间粒度及 W0 注释。 |
| `locomo_hierarchy_grounded_20261024` | `grounded.py` | 统一事实库、父层继承事实引用、FTS/导航路径、受保护低层证据。 |
| `locomo_hierarchy_relevance_r1_20261024` | `tools/build_routes.py`、`tools/r1_pipeline.py` | 同目标导航路径折叠与筛选实验。 |
| `locomo_hierarchy_relevance_r1_2_20261024` | `screen_client.py`、`support_atoms.py` | 筛选客户端及支持信息判断的中间实现。 |
| `locomo_hierarchy_relevance_r1_3b_20261024` | `r13b_prepare.py`、`r13b_fix.py` | 候选池、固定标签替换、窗口保护与原子应用。 |
| `locomo_hierarchy_relevance_full_20261025` | `full_prepare.py`、`full_run.py`、`full_answer.py` | 本轮 861 题正式实验的完整提示、选证据、回答、判分和缓存协议。 |

以上目录均位于 `F:/codex数据库memory/` 下。`full_prepare.py`、`full_run.py` 仍通过绝对本机路径及 `sys.path` 导入前几轮实验模块；所以成果目前应表述为**经离线实验验证的参考实现**。它尚不是可以直接宣称已合入 TiMEM 核心包的可移植实现。

本轮修正缓存的脚本和最终分数正在由主任务处理。PR 数字应以完成两题修正后的最终汇总为准，不复制本文撰写时的旧分数。

## 4. 贡献规范与模板

TiMEM 已有：

- `F:/worktable/research/timem/CONTRIBUTING.md`
- `F:/worktable/research/timem/.github/PULL_REQUEST_TEMPLATE.md`

模板要求描述、改动类型、具体改动文件、验证步骤、案例、相关 issue 和检查项。贡献指南要求相关文档与必要测试更新，建议 Ruff 与 `pytest tests/`；实际未运行的检查不勾选。

Bench 副本的 `CONTRIBUTING.md` 要求聚焦提交，运行 `pytest tests/unit -q`。若改变摄取粒度、并发、session ID 或评分规则，需同步更新 `BENCHMARK_PROTOCOL.md`。本轮缓存身份修正应在评分/缓存协议说明中写清楚。

在这三处源码树及其检查过的父目录中，未发现 `AGENTS.md`。`.gitignore` 存在 `D:\LLM\` 的无效 glob，常规 `rg` 会警告；本文搜索使用 `--no-ignore-vcs`，不因其忽略规则推定不存在文件。

## 5. 推荐的 PR 组织方式

### A. 当前用户要求的成果文档

当前用户要求整理从 0 到 1 的优化点、具体落地案例、修正后的结果，供 GitHub PR 引用；这并不等同于授权本次直接创建/发布 PR。

推荐先把主任务形成的 Markdown 作为可审阅成果材料。若目标为 TiMEM 仓库，建议最终落到：

```text
experiments/results/locomo_hierarchy_20261025.md
experiments/results/locomo_hierarchy_20261025/manifest.json
experiments/results/locomo_hierarchy_20261025/examples.json
```

路径是建议的新路径，当前未在仓库内创建。可在 `experiments/README.md` 增加一条链接。正文应说明数据集版本/类别分布、B/L/H 定义、模型配置、冻结输入、最终分数、配对变化、真实前后案例及离线范围。

### B. 可复现实验代码

若进一步提交实现，建议放入独立目录：

```text
experiments/datasets/locomo_hierarchy/
    build_store.py
    temporal_annotations.py
    retrieval.py
    routes.py
    select_evidence.py
    score.py
    protocol.json
    tests/
```

应从最后正式运行的调用链提炼代码，改为仓库相对路径或 CLI 参数，避免把每一轮试验目录全量复制进 PR。保留最后使用的算法和必要负例测试，并将失败尝试放进文档的取舍说明。

### C. 产品化集成另行明确边界

把事实引用/时间注释正式加入生产 schema、把高层导航接入真实 API、迁移已有数据库、与原向量检索融合等，都不应仅凭本轮离线结果写成“已完成”。若包含这些工作，需要给出真实源码 diff、迁移策略、兼容性与集成测试。

## 6. 建议提交范围和避免混入的材料

建议纳入：优化说明、最终结果表、去标识的具体案例、实验协议与文件哈希、最终运行脚本、缓存修复、与缺陷对应的测试。

不建议纳入：`.env`、密钥、带鉴权的导出/导入脚本、机器绝对路径配置、大量逐题模型响应、重复中间数据、没有实际使用的实验分支。完整公开数据的再分发仍应遵循其原许可；PR 可用下载来源与重建命令代替拷贝整份冻结数据。

措辞建议：

> 在 TiMEM 既有五层架构上，构建冻结的离线事实库，将上层摘要用于导航到可追溯的低层事实，并以逐事实时间标注、重复路径折叠、受保护窗口和模型联合选择改进证据；报告同输入 B/L/H 对照与修正缓存后的 LoCoMo refined 861 题结果。

此表述能够清楚区分原有系统能力、本轮真正实现的优化以及尚待产品化的范围。
