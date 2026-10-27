# syntax=docker/dockerfile:1.4
# TiMEM AML image — layered like timem-platform-backend (deps cached, code last).
# Build: DOCKER_BUILDKIT=1 docker compose build app

# ---------- frontend ----------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---------- backend ----------
FROM python:3.11.10-slim
WORKDIR /app

ENV PIP_DEFAULT_TIMEOUT=600 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    TIMEM_DEPLOY_MODE=server \
    TIMEM_HOST=0.0.0.0 \
    TIMEM_PORT=8000

RUN python -m pip install pip==23.3.1 --no-cache-dir

# Optional mirrors (same idea as Platform); comment out if your network blocks them
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_EXTRA_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/

# System libs for psycopg2 / asyncpg builds when wheels miss
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer (cache-friendly)
COPY requirements.txt pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements.txt

# Application code
COPY . .
COPY --from=web /web/dist /app/frontend/dist

# Editable install for package metadata / entrypoints (deps already installed)
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps -e .

RUN mkdir -p /app/logs /app/data

EXPOSE 8000
CMD ["python", "-m", "server"]
