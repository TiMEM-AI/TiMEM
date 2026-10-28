# DEPRECATED: monolith image (frontend baked into backend).
# Prefer Space-style split:
#   - frontend/Dockerfile  (nginx + SPA)
#   - server/Dockerfile    (Python API only)
# Backup of the previous multi-stage file: Dockerfile.monolith.bak
# Build via: docker compose build frontend app
FROM scratch
LABEL deprecated="use frontend/Dockerfile and server/Dockerfile"