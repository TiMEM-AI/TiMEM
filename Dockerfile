# syntax=docker/dockerfile:1.4
# TiMEM AML single image: Node builds SPA → Python(FastAPI) serves API + SPA on :8000.
# Deps (apt + pip) live in stage "deps" / Dockerfile.deps. App stage only copies code.
# Build (recommended): deploy/build_app.sh
# Build (compose only): DOCKER_BUILDKIT=1 docker compose build app
#   → default DEPS_IMAGE=deps uses in-file deps stage (yaml-only edits do NOT reinstall pip)
# Build (reuse tagged deps): --build-arg DEPS_IMAGE=${PROJECT}_deps:<hash>

# Global ARG for FROM (must sit before the first FROM)
ARG DEPS_IMAGE=deps

# ---------- frontend (cached by package-lock) ----------
FROM node:22.23.3-bookworm-slim AS web-builder
WORKDIR /web

RUN npm config set registry https://registry.npmmirror.com

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps

COPY frontend/ ./

ARG ENV_FILE=.env.production
RUN if [ -f "$ENV_FILE" ]; then cp "$ENV_FILE" .env; fi

RUN npm run build

# ---------- dependency base (apt + pip); also buildable via Dockerfile.deps ----------
FROM python:3.11.10-slim AS deps
WORKDIR /app

ENV PIP_DEFAULT_TIMEOUT=600 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    DEBIAN_FRONTEND=noninteractive \
    TIMEM_DEPLOY_MODE=server \
    TIMEM_HOST=0.0.0.0 \
    TIMEM_PORT=8000

RUN python -m pip install pip==23.3.1 --no-cache-dir

# Narrow COPY: only spec_exec — do NOT bind-mount the whole build context
COPY spec_exec ./
RUN chmod +x spec_exec && sed -i "s/\r$//" spec_exec && ./spec_exec

ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_EXTRA_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/

COPY requirements.txt pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements.txt

# ---------- runtime app (FROM deps stage by default, or a prebuilt DEPS_IMAGE) ----------
FROM ${DEPS_IMAGE}
WORKDIR /app

ENV PIP_DEFAULT_TIMEOUT=600 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    DEBIAN_FRONTEND=noninteractive \
    TIMEM_DEPLOY_MODE=server \
    TIMEM_HOST=0.0.0.0 \
    TIMEM_PORT=8000 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_EXTRA_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/

# Application code last — yaml/code edits only invalidate from here
COPY . .

COPY --from=web-builder /web/dist /app/frontend/dist

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps -e .

RUN mkdir -p /app/logs /app/data

EXPOSE 8000
CMD ["python", "-m", "server"]
