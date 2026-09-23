FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY . .
COPY --from=web /web/dist /app/frontend/dist
RUN pip install --no-cache-dir -e .

ENV TIMEM_DEPLOY_MODE=server
ENV TIMEM_HOST=0.0.0.0
ENV TIMEM_PORT=8000
EXPOSE 8000

CMD ["python", "-m", "server"]
