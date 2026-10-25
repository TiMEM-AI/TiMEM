# 全量 861 执行状态（R1 full 协议）

日期：2026-10-24
目录：`F:\codex数据库memory\locomo_hierarchy_relevance_full_20261025`
协议：V2 候选生成 + 完整 JSON 提示 + 已测交换函数（`apply_plan_fixed`）+ 完整提示缓存（`prompt` 预生成并哈希）

---

## 1. 已接入的生产路径

| 组件 | 文件 | 哈希记录 |
|---|---|---|
| 候选生成（V2：BM25 排序、h_seen 仅 B+前 8 低层） | `r13b_prepare.py` | RUN_MANIFEST |
| 证据应用（多尾部删除修复、禁止静默截断） | `r13b_fix.py` | RUN_MANIFEST |
| 提示模板（完整 JSON、无截断） | `full_prepare.py` | `prompt_template_sha` |
| 交换校验 + 原子应用 | `full_run.py` | 同目录 |
| 答案/判分（同证据共享） | `full_answer.py` | 同目录 |

每份请求保存了**完整提示文本**与 `prompt_sha`、字符数；运行器直接使用该文本，不再有字符串切片。

## 2. 离线准备结果

```text
题数            861
L 计划任务       407
H 计划任务       407
guard_skip      454（两臂逐字回退 B，不产生调用）
```

`RUN_MANIFEST.json` 记录：协议参数（24 窗口、20 保护、L 12、H 8+4、预算 24k token、最多 2 次重试、模型）、
三个代码文件哈希、提示模板哈希。

## 3. 全量启动自检（前 4 个任务）

```text
conv-26#q0002:L  valid  fallback=False  sel=[]
conv-26#q0002:H  valid  fallback=False  sel=['c3']
conv-26#q0007:L  valid  fallback=False  sel=[]
conv-26#q0007:H  valid  fallback=False  sel=['c9', 'c12', 'c4']
```

- 4/4 有效，无回退；
- H 臂实际选入了高层候选（c3；c9/c12/c4），说明 V2 修复后的高层入口已生效；
- 证据文件按臂落盘，guard_skip 任务逐字复制 B。

## 4. 全量运行状态

已在后台启动 `full_run.py`，状态目录为 `state.json`，全程断点续跑：

```text
已完成：4 / 814 逻辑任务（407×2）
预计：约 8.5 小时（75 秒/次，串行）
失败处理：网络/技术错误最多重试 2 次，仍失败则整次回退 B 并写入 errors，不剔除分母
```

重新启动（自动跳过已完成）：

```powershell
$py = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
cd F:\codex数据库memory\locomo_hierarchy_relevance_full_20261025
& $py -B full_run.py
```

## 5. 完成后执行答题与判分

```powershell
& $py -B full_answer.py
```

输出：`answers/`（逐题 B/L/H）、`RESULTS_FULL.json`（四类与总分、唯一评分次数、L−B / H−L 配对、模型名）。

## 6. 交付物（运行完成后齐备）

```text
RUN_MANIFEST.json       代码/模板哈希、协议参数
requests/*.json         861×2 完整提示与哈希
evidence/{B,L,H}/       逐题证据（guard/失败均为显式 B 副本）
responses.jsonl         每次调用的原始响应、prompt_sha、usage
state.json              逐任务终态、回退原因、重试次数
answers/                逐题答案与正确性
RESULTS_FULL.json       四类/总分、配对、唯一评分次数、模型
```
