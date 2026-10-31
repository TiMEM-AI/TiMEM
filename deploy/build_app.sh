#!/usr/bin/env bash
# Build AML app image with a content-addressed deps base image.
# Safe to call from server deploy_run.sh instead of bare `docker compose build app`.
# Usage (from repo root on the deploy host):
#   PROJECT_NAME=timem_aml COMMIT=abc1234 bash deploy/build_app.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PROJECT_NAME="${PROJECT_NAME:?PROJECT_NAME is required}"
COMMIT="${COMMIT:-latest}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

hash_inputs() {
  # Hash only files that should force a deps rebuild
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum Dockerfile.deps spec_exec requirements.txt pyproject.toml | sha256sum | cut -c1-12
  else
    # macOS / busybox fallback
    cat Dockerfile.deps spec_exec requirements.txt pyproject.toml | shasum -a 256 | cut -c1-12
  fi
}

DEPS_TAG="${DEPS_TAG:-$(hash_inputs)}"
DEPS_IMAGE="${PROJECT_NAME}_deps:${DEPS_TAG}"
APP_IMAGE="${PROJECT_NAME}_app:${COMMIT}"

echo "[build_app] DEPS_IMAGE=${DEPS_IMAGE}"
echo "[build_app] APP_IMAGE=${APP_IMAGE}"

if docker image inspect "${DEPS_IMAGE}" >/dev/null 2>&1; then
  echo "[build_app] deps image exists — skip Dockerfile.deps rebuild"
else
  echo "[build_app] building deps image (requirements/spec_exec changed or first build)"
  DOCKER_BUILDKIT=1 docker build \
    -f Dockerfile.deps \
    -t "${DEPS_IMAGE}" \
    --build-arg BUILDKIT_INLINE_CACHE=1 \
    .
fi

# Also tag :latest for compose defaults / local convenience
docker tag "${DEPS_IMAGE}" "${PROJECT_NAME}_deps:latest"

export DEPS_TAG
export DEPS_IMAGE
export COMMIT
export PROJECT_NAME

echo "[build_app] building app from ${DEPS_IMAGE}"
COMPOSE_BAKE=true DOCKER_BUILDKIT=1 docker compose -f "${COMPOSE_FILE}" build \
  --build-arg "DEPS_IMAGE=${DEPS_IMAGE}" \
  --build-arg BUILDKIT_INLINE_CACHE=1 \
  app

echo "[build_app] done → $(docker image inspect -f '{{.RepoTags}} {{.Size}}' "${APP_IMAGE}" 2>/dev/null || echo "${APP_IMAGE}")"
