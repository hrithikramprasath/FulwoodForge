# Locked application dependencies; refreshed OS packages and Chrome on each scheduled rebuild.
FROM node:22-bookworm-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ARG VITE_API_URL=""
ENV VITE_API_URL=$VITE_API_URL
RUN npm run lint && npm run build

FROM node:22-bookworm-slim AS renderer
WORKDIR /renderer
COPY frontend/renderer/package.json frontend/renderer/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM ghcr.io/astral-sh/uv:0.12.4 AS uv
FROM python:3.12-slim-trixie
LABEL org.opencontainers.image.title="Fulwood Forge"
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=web /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg poppler-utils pandoc \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
      | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
RUN echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
      > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get purge -y curl gnupg && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache \
    && python -m pip uninstall -y pip && rm /usr/local/bin/uv
ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONPATH="/app/backend" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/appuser PORT=8000 ARTIFACTS_DIR=/tmp/artifacts
COPY backend/ ./
COPY --from=web /app/frontend/renderer/render.mjs /app/frontend/renderer/render.mjs
COPY --from=renderer /renderer/node_modules /app/frontend/renderer/node_modules
COPY --from=web /app/frontend/dist /app/frontend/dist
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data && chown appuser:appuser /app/data
EXPOSE 8000
USER appuser
CMD ["sh", "-c", "umask 077; mkdir -p /tmp/artifacts; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-server-header"]
