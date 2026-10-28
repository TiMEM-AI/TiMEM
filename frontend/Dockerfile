# syntax=docker/dockerfile:1.4
# TiMEM AML frontend — Node build + nginx (Space-style split).
# Build: DOCKER_BUILDKIT=1 docker compose build frontend

FROM node:22-alpine AS builder
WORKDIR /app

# Optional mirror; comment out if your network blocks it
RUN npm config set registry https://registry.npmmirror.com

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx.conf listens on 3000 (matches Space compose mapping FRONTEND_PORT:3000)
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]