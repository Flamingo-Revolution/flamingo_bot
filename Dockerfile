FROM node:24-bookworm-slim AS widget

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run check && npm test && npm run build

FROM ghcr.io/astral-sh/uv:0.11.8 AS uv

FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    FLAMINGO_ENVIRONMENT=production \
    FLAMINGO_WIDGET_DIR=/app/widget

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --locked --no-dev

COPY openai.yaml ./openai.yaml
COPY config/ ./config/
COPY --from=widget /build/frontend/dist/ ./widget/

RUN useradd --system --uid 10001 --create-home flamingo
USER 10001

EXPOSE 8080
CMD ["uvicorn", "flamingo_bot.api:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
