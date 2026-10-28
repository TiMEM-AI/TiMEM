# syntax=docker/dockerfile:1.4
# TiMEM AML backend — Python API only (no Node stage; Space-style split).
# Build context MUST be the repo root (needs timem/, server/, llm/, …):
#   DOCKER_BUILDKIT=1 docker compose build app
#   # compose: context: .  dockerfile: server/Dockerfile

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

# Optional mirrors (same idea as Platform / Space); comment out if blocked
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_EXTRA_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/

RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer (cache-friendly)
COPY requirements.txt pyproject.toml README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements.txt

# Application code only — frontend is a separate image (no frontend/dist copy)
COPY server/ ./server/
COPY timem/ ./timem/
COPY llm/ ./llm/
COPY storage/ ./storage/
COPY services/ ./services/
COPY tools/ ./tools/
COPY config/ ./config/
COPY migration/ ./migration/

# Editable install for package metadata / entrypoints (deps already installed)
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps -e .

RUN mkdir -p /app/logs /app/data

EXPOSE 8000
CMD ["python", "-m", "server"]