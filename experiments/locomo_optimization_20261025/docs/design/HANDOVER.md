# TiMEM × AML 文本赛道 —— 任务交接文档

> 最后更新：2026-10-22 15:30（Asia/Shanghai）
> 交接给：下一位接手本任务的 Agent / 工程师
> 一句话：在本地复现 AML 文本赛道的评测链路，优化 TiMEM 在 **LoCoMo-Refined** 上的表现，当前稳定基线 **80.5%（三次中位数）**。

---

## 0. 三十秒速览

| 项 | 状态 |
|---|---|
| 目标 | CSIG Agent Memory Challenge（文本赛道），在 TiMEM `AML` 分支上做检索优化 |
| 当前数据集 | LoCoMo-Refined `conv-26`（87 题，daily 卷） |
| 稳定基线 | **80.5%**（79.3 / 80.5 / 83.9，温度 0，三次中位数） |
| 最大单项收益 | 时间类题目 **20% → 60%**（日期锚点 + 相对时间解析） |
| 当前最弱项 | 多跳 67%、时间 60% |
| 运行状态 | **服务/Docker/shim 均已停止**，需按第 3 节恢复 |
| 关键风险 | 模型 key（DeepSeek 可用、金山云被网关限流）、L3 从未生成过 |

---

## 1. 任务背景（为什么做这些）

- 比赛只让我们实现两个接口：`Add`（写入记忆）和 `Search`（返回证据），答题与判分由平台统一做。
- 官方文本赛道有 6 个数据集；**我们只负责 ScriptMem**，但 ScriptMem 公开包**没有语料**（只有题目），所以先用 LoCoMo-Refined 做通用优化，结论再迁移。
- 因此本任务的目标不是"刷 ScriptMem 的分"，而是：**把 TiMEM 的通用缺陷找出来并修掉**，同时沉淀一份可给其他数据集负责人复用的改造清单（见 `doc/timem-time-optimization-note.md`）。

---

## 2. 目录与关键文件

### 2.1 两个主仓库

```
F:\worktable\research\
├── timem-aml\      # TiMEM 的 AML 分支（被测系统，Python/FastAPI）
├── bench\          # AML 评测框架（agent-memory-benchmark）
├── web\            # 抓下来的 AML 官网/太忆控制台前端资源（只读参考）
└── scriptmem\      # ScriptMem 公开包（只有题目，无语料）
```

### 2.2 文档与脚本

```
F:\worktable\doc\
├── timem-time-optimization-note.md   # ★ 给同事看的参考意见（可直接转发）
├── Agent记忆系统入门与参赛设计手册.docx # 早期教学材料（背景知识）
├── baseline_stats.py                 # 三次运行的统计脚本
├── export_to_space.py                # 把本地记忆批量导入太忆空间
└── ...
```

### 2.3 改动过的文件（**这是接手时最需要先看的**）

#### timem-aml（被测系统）

| 文件 | 改动 | 原因 |
|---|---|---|
| `server/time_anchor.py` | **新增** | 时间戳注入 + 确定性日期打标 + 相对时间解析 |
| `server/ownership.py` | **新增** | 检索出口按 user_id 硬校验（合规底线） |
| `server/l2_backfill.py` | **新增** | add 后防抖触发 L2 补齐 + 覆盖集幂等 |
| `server/search_expand.py` | **新增** | 一跳实体扩展检索（先搜→抽实体→再搜→合并封顶） |
| `server/app.py` | 改 | 接入上面四个模块；加 logger |
| `server/engine.py` | 未改 | — |
| `llm/xingliu_adapter.py` | 改 | ① 传输改 curl 子进程 ② `asyncio.to_thread` 避免阻塞事件循环 ③ embedding 走独立 base_url ④ 去掉 thinking 参数 |
| `llm/embedding_service.py` | 改 | 本地 Qwen 适配器改惰性导入（否则强制依赖 torch） |
| `timem/workflows/retrieval_nodes/semantic_retriever.py` | 改 | 向量查询显式带 `user_id` 过滤 |
| `config/settings.yaml` | 改 | `max_retries: 3→0`（防重试风暴）；`temperature: 0.8→0`（可复现） |
| `.env` | 改 | 对话=DeepSeek，向量=金山云，本地服务 token |
| `data/model_settings.json` | **新增** | 分槽位配置：reply/memory=DeepSeek，embed=金山云 2048 维 |
| `deploy/docker-compose.local.yml` | **新增** | 把 PG/Qdrant 端口映射到宿主机 |
| `run_smoke.ps1` / `try_add_search.ps1` | **新增** | 单次 add/search 自检脚本（纯 ASCII） |
| `shim.py` | **新增但已废弃** | 曾用于绕过金山云网关 403，换 DeepSeek 后不再需要 |

#### bench（评测框架）

| 文件 | 改动 | 原因 |
|---|---|---|
| `.env` | 改 | 答题/判分指向 DeepSeek；AML 地址指向 `127.0.0.1:8000/aml/*` |
| `benchmark_data/cache/locomo_refined.json` | **手动放入** | 官方 URL 被墙，用 codeload 下载 |
| `run_three.ps1` | **新增** | 连跑三次取中位数 |

> ⚠️ 这两个目录都是**克隆副本**，没有 git 上游关系。改动没有提交，需要的话自行 `git diff`（bench 和 timem-aml 都有 .git）。

---

## 3. 从零恢复（当前状态：全部停止）

### 3.1 启动 Docker 与数据库

```powershell
# 1) 启动 Docker Desktop（必须等引擎就绪，约 30-60s）
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
# 等待直到 `docker ps` 不报错

# 2) 起 Postgres + Qdrant（带端口映射）
cd F:\worktable\research\timem-aml
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml up -d postgres qdrant

# 3) Neo4j（图库，add 时必须，否则报 No module/连接错误）
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml --profile graph up -d neo4j

# 4) Redis（compose 里没有，手动补，端口必须 16379）
docker run -d --name deploy-redis-1 -p 127.0.0.1:16379:6379 redis:7-alpine
```

端口对照：Postgres `15432`、Qdrant `16333`、Neo4j `17687`、Redis `16379`。

> 若 Docker 拉不到镜像：检查 WinHTTP 代理（`netsh winhttp show proxy`）。历史上有一次残留代理 `192.168.43.1:7890` 导致全部拉取失败，需要管理员执行 `netsh winhttp reset proxy`。镜像源已在 `~/.docker/daemon.json` 配了 daocloud + dockerproxy。

### 3.2 启动 TiMEM 服务

```powershell
$py = "C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
cd F:\worktable\research\timem-aml
Start-Process -FilePath $py -ArgumentList "-m","server" -WorkingDirectory "F:\worktable\research\timem-aml" `
  -RedirectStandardOutput "$env:TEMP\timem.out" -RedirectStandardError "$env:TEMP\timem.err" -WindowStyle Hidden
Start-Sleep 15
curl.exe -sS -m 5 -o NUL -w "health=%{http_code}`n" "http://127.0.0.1:8000/health"   # 期望 200
```

### 3.3 冒烟验证（一条命令看全链路）

```powershell
cd F:\worktable\research\timem-aml
powershell -ExecutionPolicy Bypass -File .\try_add_search.ps1 `
  -Text "Alice is a nurse in Hangzhou." -Query "Where does Alice work?" -User u-smoke-01
```

期望看到：`add http=200`、库里出现 L1、`search http=200` 且返回 1~N 条含该句的记忆。

---

## 4. 评测怎么跑

### 4.1 单次（约 20 分钟）

```powershell
cd F:\worktable\research\bench
python main.py aml --dataset locomo_refined --limit 1 --run-id <run-name>
```

### 4.2 三次取中位数（约 60 分钟，**做对比时必须**）

```powershell
cd F:\worktable\research\bench
pwsh -NoProfile -ExecutionPolicy Bypass -File .\run_three.ps1
```

### 4.3 报告结构

```
bench/reports/<run-id>/
├── aml_add.json            写入明细（chunk 数、每条 success）
├── aml_questions.json      本次的题目
├── aml_search.json         每题检索结果（含 data 数组、延迟）
├── aml_score.partial.json  评分过程（可观察进度）
└── aml_score.json          最终分（correct/total/accuracy + 逐题 gold/generated/category）
```

读分：

```python
import json, collections
d = json.load(open(r"...\aml_score.json", encoding="utf-8"))
print(d["correct"], d["total"], d["accuracy"])
per = collections.defaultdict(lambda: [0,0])
for r in d["results"]:
    c = str(r["category"]); per[c][1] += 1
    if r["correct"]: per[c][0] += 1
print(dict(per))
```

---

## 5. 当前基线（可直接引用）

### 5.1 总分演进

| 运行 | 配置 | 总分 |
|---|---|---|
| run2 | 原始（温度 0.8） | 59/87 = 67.8% |
| run3 | + 日期锚点 | 62/87 = 71.3% |
| run3b | + 相对时间解析（只重检索） | 63/87 = 72.4% |
| run4 | 同 run3b，完整重跑 | 71/87 = 81.6% |
| **run5/6/7** | **温度 0，三次** | **69 / 70 / 73 = 79.3 / 80.5 / 83.9** |

> **稳定基线 = 80.5%（中位数）**，极差 4.6 分。
> run2 含两个变量（温度 + 时间修复），不能简单说"67.8→80.5"。

### 5.2 分类别（run5/6/7 均值）

| 类别 | 准确率 |
|---|---|
| 3 推理 | 95% |
| 4 单跳 | 91% |
| 1 多跳 | **67%** |
| 2 时间 | **60%** |

---

## 6. 已确立的四条结论

1. **时间信息是最大失分点，且可修**：错误 100% 是"金标绝对日期、模型答相对时间"。修法 = 写入时把日期塞进内容并解析相对表达（`server/time_anchor.py`）。时间类 20% → 60%。
2. **生成温度必须归零**：0.8 时同样配置两次跑出 72.4 与 81.6；归零后极差缩到 4.6 分。**所有对比必须≥3 次取中位数。**
3. **跨 user_id 泄漏是真实存在的系统性缺陷**：语义检索没带 user_id、BM25 链路没透传。已在出口加硬校验（失败即返回空）。
4. **L2 会重复汇总**：同一批 L1 会产出多条内容相同的 L2。已加"覆盖集幂等"（3 条 L1 → 1 条 L2）。

---

## 7. 已知问题与欠债（接手第一优先）

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | **L3/L4/L5 从未生成过** | 多跳缺"跨会话脉络"，只能靠 L1 硬拼 | `server/l2_backfill.py` 的 `LAYERS = ["L2"]` 改成 `["L2","L3"]` 后跑一次对比 |
| 2 | 代码已改但**未重启验证** | 打标已扩到 L1–L5、函数改名 `stamp_temporal`，服务停止前未加载 | 下次启动后跑一次 add，确认 L1 仍带日期 |
| 3 | BM25 检索的 user 过滤**根因未修** | 出口校验兜住了，但会丢召回 | 追 `storage/memory_storage_manager.search_memories` 的 adapter 透传 |
| 4 | `run/first-run` 是失败记录 | 当时服务被同步 subprocess 阻塞 | 已修（`asyncio.to_thread`），可忽略该目录 |
| 5 | 金山云 key 疑似被限流 | 服务进程调用返回 403 `unauthorized consumer` | 已改用 DeepSeek 做对话；向量仍用金山云（待观察是否复发） |
| 6 | 只跑过 1 段对话（conv-26） | 结论的泛化性未验证 | 跑 861 题全量卷 |
| 7 | 未接 ScriptMem | 无公开语料 | 见 `research/scriptmem`，需自建语料 |

---

## 8. 下一步建议（按性价比）

1. **开 L3，跑一次对比**（30 分钟）：验证多跳是否受益。
2. **钻多跳错题**（当前 67%）：看是"检索没召回"还是"召回了但答错"，前者调 `search_expand.py` 的实体策略，后者改证据组织。
3. **钻剩余 8 道时间错题**：大概率是"跨会话日期比较"，需要另一套处理（不是打标能解决）。
4. **上 861 题全量卷**：把噪声压到 ±2 分以内，作为发版基线。
5. **接 ScriptMem**：需要重建 4 部作品的语料（其中《人民公敌》是公版，可先从 Gutenberg 做）。

---

## 9. 踩过的坑（省你几小时）

| 现象 | 根因 | 处置 |
|---|---|---|
| `docker compose up` 拉镜像超时 | WinHTTP 残留代理 | 管理员 `netsh winhttp reset proxy`；或用 daocloud 镜像 |
| `No module named 'neo4j' / 'redis'` | requirements 里被注释 | `pip install neo4j redis` |
| `No module named 'torch'` | embedding_service 顶层硬导入本地 Qwen | 已改惰性导入 |
| Postgres 表建不出来 | 缺 `greenlet`（SQLAlchemy async 依赖） | `pip install greenlet` |
| Qdrant 报 `no attribute 'search'` | 客户端版本过新 | 固定 `qdrant-client==1.15.1` |
| 服务跑着跑着无响应 | `subprocess.run` 阻塞事件循环 | 已改 `asyncio.to_thread` |
| 数据集下载 `ConnectError` | `raw.githubusercontent.com` 被墙 | 用 `codeload.github.com` 打包下载后放进 `benchmark_data/cache/` |
| PowerShell 脚本中文乱码/语法错 | UTF-8 无 BOM 被按 GBK 读 | 脚本保持纯 ASCII |
| 评测 0 结果 | 服务阻塞 + 检索无过滤 | 见上两条 |
| `created_at` 拿不到"刚写入"的行 | 存的是**会话时间**不是写入时间 | 用 `time_window_start` 做锚点 + 幂等条件 |

---

## 10. 关键凭证与配置位置

| 用途 | 位置 | 说明 |
|---|---|---|
| 对话模型（DeepSeek） | `timem-aml/.env` → `XINGLIU_API_KEY` / `OPENAI_API_KEY` | base `https://api.deepseek.com/v1`，模型 `deepseek-chat` |
| 向量模型（金山云） | `timem-aml/.env` → `XINGLIU_EMBED_*` | `qwen3-embedding-8b`，2048 维；`QDRANT_VECTOR_SIZE=2048` |
| 本地服务鉴权 | `TIMEM_API_KEY=[REDACTED_AML_API_KEY]` | 评测框架 `AML_API_KEY` 必须一致 |
| 评测框架模型 | `bench/.env` → `ARK_*` / `JUDGE_*` / `ANSWER_*` | 全部指向 DeepSeek |
| 太忆空间 MCP | `~/.dsh/.credentials.yaml` → `TIMEM_API_KEY`（tmk_ 开头） | 供 `mcp__timem__*` 工具；**注意凭证名只能字母数字下划线** |
| 太忆空间导入 | `doc/export_to_space.py` | 批量 CSV 导入；CSV 表头**不能加引号**，否则报"文件为空" |

> ⚠️ `.env` 里是明文密钥，不要提交到任何仓库；交接时建议轮换。

---

## 11. 接手第一小时建议流程

1. 读本文第 3 节，把环境拉起来，跑通 `try_add_search.ps1`（10 分钟）
2. 读 `doc/timem-time-optimization-note.md`，理解时间改造（10 分钟）
3. 跑一次 `python main.py aml --dataset locomo_refined --limit 1 --run-id sanity`，对照第 5 节看是否落在 79–84% 区间（20 分钟）
4. 若复现成功，从第 8 节挑一项开始（建议先开 L3，因为改动最小、收益方向明确）

---

## 12. 一句话交接

> 链路已通、基线已稳（80.5%）、时间类已从 20% 修到 60%；
> 下一步优先验证 **L3 是否值得开**，然后攻 **多跳（67%）**；
> 任何对比都要 **温度 0 + 三次中位数**，否则数字没意义。
