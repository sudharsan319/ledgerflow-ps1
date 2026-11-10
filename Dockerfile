FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend ./backend
COPY scripts/container-start.sh ./scripts/container-start.sh
COPY --from=frontend /web/dist ./frontend/dist
RUN useradd --create-home --uid 10001 ledgerflow && mkdir /app/data && chown ledgerflow:ledgerflow /app/data
USER ledgerflow
ENV LEDGERFLOW_DATA_DIR=/app/data
EXPOSE 8000
CMD ["sh", "/app/scripts/container-start.sh"]
