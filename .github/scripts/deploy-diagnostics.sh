#!/usr/bin/env bash
# Sourced by deploy workflow (same contract as timem-platform-backend).
# Diagnostics only on stdout/stderr; never print secrets or enable xtrace.
set +x
set -Eeuo pipefail

DEPLOY_STAGE=initialization
DEPLOY_FAILURE_LINE=unknown

deploy_stage() {
  if [[ "$DEPLOY_STAGE" != initialization ]]; then
    printf '[%s] [runner] stage=%s status=success exit=0\n' "$(date -u +%FT%TZ)" "$DEPLOY_STAGE"
  fi
  DEPLOY_STAGE=$1
  printf '[%s] [runner] stage=%s status=start\n' "$(date -u +%FT%TZ)" "$DEPLOY_STAGE"
}

deploy_exit() {
  local exit_code=$1
  local exit_line=$2
  trap - EXIT
  if [[ -n "${DEPLOY_TEMP_ENV_FILE:-}" ]]; then
    rm -f -- "$DEPLOY_TEMP_ENV_FILE" || true
  fi
  if (( exit_code == 0 )); then
    printf '[%s] [runner] stage=%s status=success exit=0\n' "$(date -u +%FT%TZ)" "$DEPLOY_STAGE"
  else
    printf '[%s] [runner] stage=%s status=failed exit=%s line=%s\n' "$(date -u +%FT%TZ)" "$DEPLOY_STAGE" "$exit_code" "${DEPLOY_FAILURE_LINE/unknown/$exit_line}" >&2
  fi
  exit "$exit_code"
}

trap 'DEPLOY_FAILURE_LINE=$LINENO' ERR
trap 'deploy_exit "$?" "$LINENO"' EXIT

SSH_OPTIONS=(
  -v
  -i "$HOME/.ssh/id_rsa"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o ConnectTimeout=15
  -o ConnectionAttempts=1
  -o StrictHostKeyChecking=accept-new
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
)
