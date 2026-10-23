# Build context: repository root.
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /usr/local/bin/uv

WORKDIR /app/server
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev
COPY server/ ./
COPY --from=web /web/dist /app/web/dist

ENV PROJECTOR_DATA_DIR=/data \
    PROJECTOR_WEB_DIST=/app/web/dist \
    PROJECTOR_PORT=8000
VOLUME /data
EXPOSE 8000
CMD ["/app/server/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
