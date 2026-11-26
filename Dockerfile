# pal for Linux and macOS (Docker). Windows uses the native setup in the README instead.
# Built and run by scripts/pal-docker.sh with compose.docker.yaml; see docs in the README.

# --- web app ---
FROM node:22-slim AS web
WORKDIR /web
RUN corepack enable && corepack prepare pnpm@11.2.2 --activate
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# --- server ---
FROM python:3.13-slim-bookworm
# Postgres 18 client tools (pg_dump, psql, pg_restore) for backups; Debian's own are too old for a PG 18 server.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d /usr/share/keyrings \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-18 \
 && apt-get purge -y curl gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
COPY migrations/ migrations/
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist web/dist

# The container runs as the host user (compose sets `user:`), so secrets and backups on the host belong to them.
ENV PATH="/app/.venv/bin:$PATH" \
    PAL_CONTAINER=1 \
    PAL_HOST=0.0.0.0 \
    PAL_DATA_DIR=/data \
    PAL_SECRETS_DIR=/run/pal-secrets \
    HOME=/tmp
EXPOSE 8765
CMD ["pal", "serve"]
