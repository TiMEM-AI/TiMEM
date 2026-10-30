# TiMEM AML 分支服务器部署

与 `timem-platform-backend` 使用同一套 GitHub Actions 部署模板：**rsync → 写入 `.env` → Docker Compose**。

生产栈文件在**仓库根目录** `docker-compose.yml`（服务名 `app` / `postgres` / `qdrant` / `neo4j` / `autoheal`）。  
`deploy/docker-compose.yml` 仅用于本机/笔记本联调，不走 CI。

## 1. 你需要和运维对齐的（业务侧）

| 项 | 说明 |
|----|------|
| 部署对象 | 本仓库 **`AML` 分支**，自托管 `python -m server`（含 `/aml/add`、`/aml/search`、`/health`） |
| 对外访问 | 域名 + HTTPS 反代到 **`APP_PORT`（容器 8000）** |
| 图谱 | 默认栈含 **Neo4j**；暂不需要则 Variable 去掉 `neo4j`，并从 compose 中移除该服务 |
| 模型 | 由 `.env` / Secret 配置；开源榜需按官网指定模型（见下方占位注释） |

## 2. 运维在 GitHub 仓库配置的 Secrets / Variables

仓库：`TiMEM-AI/TiMEM`（Settings → Secrets and variables → Actions）

### Secrets（分支名为 `AML` → 前缀 `AML_`）

| 名称 | 内容 |
|------|------|
| `TEST_SSH_KEY` | 与现有 TiMEM Platform 共用的部署私钥（若已存在可复用） |
| `AML_HOST` | 目标服务器 IP 或主机名 |
| `AML_USERNAME` | SSH 用户（如宝塔部署用户） |
| `AML_ENV_FILE` | **整份**生产 `.env` 文本（见下方模板，勿提交 Git） |

### Variables

| 名称 | 建议值 |
|------|--------|
| `AML_APP_PORT` | 宿主机映射端口，如 `8000` 或 `18000`（由 Nginx 反代） |
| `AML_DEPEND_SERVICES` | `postgres qdrant neo4j app` |
| `RUNNER_LABEL` | （可选）自托管 runner；默认 `ubuntu-latest` |

服务器目录：`/www/wwwroot/timem_aml`（仓库名小写 + `_` + 分支名）。

## 3. `AML_ENV_FILE` 内容模板

复制后改密码与 Key。容器内 DB/向量地址由根目录 `docker-compose.yml` 注入，**不要**在 Secret 里写 `localhost`。

```bash
TIMEM_DEPLOY_MODE=server
TIMEM_API_KEY=请生成强随机字符串

POSTGRES_USER=timem_user
POSTGRES_PASSWORD=请修改
POSTGRES_DATABASE=timem_db

NEO4J_PASSWORD=请修改
NEO4J_USER=neo4j

QDRANT_VECTOR_SIZE=2048

# ---- 模型（用户稍后自行配置；以下为占位，勿提交真实 Key）----
# 当前默认星流 OpenAI 兼容（商业/自测可用）：
XINGLIU_API_KEY=
XINGLIU_BASE_URL=https://kspmas.ksyun.com/v1
XINGLIU_CHAT_MODEL=glm-5.3-flash
XINGLIU_REPLY_MODEL=qwen3.7-flash
XINGLIU_EMBED_MODEL=qwen3-embedding-8b
XINGLIU_EMBED_DIM=2048

# 开源方法榜正式评测时需改为官网指定（自行替换上方或改服务读取逻辑）：
# LLM: gpt-4o-mini
# Embedding: text-embedding-v4（维度须与 QDRANT_VECTOR_SIZE / 集合一致）
# OPENAI_API_KEY=
# OPENAI_BASE_URL=
```

## 4. 服务器前置条件

- Docker + Docker Compose v2
- 开放 SSH（Actions runner 能连上）
- 建议 **4 核 / 16GB+** 内存（含 Neo4j）
- 宝塔/Nginx：反代到 `127.0.0.1:${AML_APP_PORT}`，配置 TLS
- 对外报名 URL 示例：`https://<域名>/aml/add`、`/aml/search`、`/health`

## 5. 发布方式

- 推送到 **`AML` 分支** 自动部署
- 或在 Actions 里手动 **Run workflow**

流程与 Platform 一致：checkout → SSH → rsync（按 `.dockerignore` 排除）→ 下发 `.env` → 远端 compose up。

## 6. 验收

```bash
curl -sS "http://127.0.0.1:${APP_PORT}/health"
# 带 Memory System Key 调 /aml/add、/aml/search；或浏览器打开 /
```

## 7. 与 Platform 的差异（刻意简化）

| | Platform backend | 本仓库 AML |
|--|------------------|------------|
| 应用 | FastAPI + Celery + 独立 frontend | 单进程 `python -m server`（内置前端静态） |
| 依赖 | MySQL/Redis/RabbitMQ/Chroma 等 | Postgres + Qdrant + Neo4j |
| 分支 Secret 前缀 | `test_` / `main_` … | `AML_` |
| 路径 | `/www/wwwroot/<repo>_<branch>` | 同左 → `timem_aml` |

## 8. 热切换记忆模型（无需重新部署）

比赛接口 `/aml/add`、`/aml/search` 解析记忆模型的顺序：**`data/runtime_overrides.json` > 环境变量 `XINGLIU_CHAT_MODEL`**。进程用内存缓存 + 文件 mtime 刷新，改完下一请求即生效，一般不必重启容器。

| 项 | 说明 |
|----|------|
| 管理口令 | `TIMEM_ADMIN_PASSWORD`（或同义 `TIMEM_ADMIN_KEY`），与报名 `TIMEM_API_KEY` **分开**；勿发给参赛方、勿提交 Git |
| 前端 | 打开站点 →「模型」→ 管理员登录 → 改比赛记忆模型 →「应用到比赛接口」 |
| 管理 API | `POST /v1/admin/login`；`GET/PUT /v1/admin/runtime-models`（Header：`X-Admin-Key`） |
| 禁止热切 | embedding / 向量维度（改了要重建 Qdrant 集合） |
| 方法榜锁模 | 设 `TIMEM_AML_LOCK_MODELS=1` 后管理写接口返回 403；可先清除 override |

`AML_ENV_FILE` 模板请增加（示例值请自行换成强随机）：

```bash
TIMEM_ADMIN_PASSWORD=请生成强随机字符串
TIMEM_RUNTIME_OVERRIDES_PATH=data/runtime_overrides.json
# TIMEM_AML_LOCK_MODELS=1
```

报名 Memory System Key **不能**改比赛模型；只有管理员口令可以。
