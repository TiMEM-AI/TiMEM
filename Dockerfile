# syntax=docker/dockerfile:1.4
# TiMEM AML 单镜像：Node 构建前端产物 → Python(FastAPI) 同时托管 API 与 SPA，仅暴露 8000 一个端口。
# 基础镜像与构建钩子对齐 TiMEM-Space 的 frontend/backend Dockerfile；
# 但不拆成两个镜像、不引入 nginx 第二端口（本项目由 server/app.py 托管 frontend/dist）。
# Build: DOCKER_BUILDKIT=1 docker compose build app

# ---------- 前端构建（产物在最后拷入后端镜像，不单独运行） ----------
FROM node:22.23.3-bookworm-slim AS web-builder
WORKDIR /web

# npm 镜像加速
RUN npm config set registry https://registry.npmmirror.com

# 先复制依赖清单（利用 Docker 分层缓存）
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps

# 复制项目代码
COPY frontend/ ./

# 通过构建参数指定环境文件名（如 .env.production）；文件不存在时跳过
ARG ENV_FILE=.env.production
RUN if [ -f "$ENV_FILE" ]; then cp "$ENV_FILE" .env; fi

RUN npm run build

# ---------- 后端运行（唯一最终镜像，单进程单端口） ----------
FROM python:3.11.10-slim
WORKDIR /app

ENV PIP_DEFAULT_TIMEOUT=600 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    DEBIAN_FRONTEND=noninteractive \
    TIMEM_DEPLOY_MODE=server \
    TIMEM_HOST=0.0.0.0 \
    TIMEM_PORT=8000

# 先固定 pip 版本（防止缓存失效、全量重装）
RUN python -m pip install pip==23.3.1 --no-cache-dir

# 执行项目自定义的系统依赖脚本（apt 安装、二进制下载等）。
# 用 bind mount 判断 spec_exec 是否存在，不存在时静默跳过（COPY 无法容忍源缺失）。
RUN --mount=type=bind,target=/ctx \
    if [ -f /ctx/spec_exec ]; then cp /ctx/spec_exec . && chmod +x spec_exec && ./spec_exec; fi

# pip 镜像加速
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_EXTRA_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/

# 依赖层（缓存友好，代码变化不破坏）
COPY requirements.txt pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements.txt

# 额外依赖：--no-deps 只装包本身，避免触发依赖解析（文件不存在时跳过）
RUN --mount=type=bind,target=/ctx \
    --mount=type=cache,target=/root/.cache/pip \
    if [ -f /ctx/requirements_nodeps.txt ]; then \
    cp /ctx/requirements_nodeps.txt . \
    && python -m pip install --no-deps -r requirements_nodeps.txt; \
    fi

# 项目代码
COPY . .

# 前端构建产物交给 FastAPI 静态托管（server/app.py 挂载 /app/frontend/dist）
COPY --from=web-builder /web/dist /app/frontend/dist

# 可编辑安装包元数据 / 入口（依赖已装好，不重复解析）
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps -e .

RUN mkdir -p /app/logs /app/data

EXPOSE 8000
CMD ["python", "-m", "server"]
