# WOMM API image (Railway or any container host). The claude_code backend is local-only;
# deployed system versions must use the `api` backend.
FROM node:22-slim AS console
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Type checking includes the tests, whose fixture is the sample run in docs/ui.
COPY docs/ui/sample_run.json /docs/ui/sample_run.json
RUN npm run build

FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY prompts ./prompts
COPY system_versions ./system_versions
COPY data ./data
COPY --from=console /web/dist ./web/dist
RUN uv sync --frozen --no-dev

RUN useradd --create-home womm
USER womm
ENV PATH="/app/.venv/bin:$PATH" WOMM_SYSTEM_VERSION=system_versions/v0.3-api.yaml
EXPOSE 8000
CMD ["sh", "-c", "uvicorn --factory womm.api.app:create_app --host 0.0.0.0 --port ${PORT:-8000}"]
