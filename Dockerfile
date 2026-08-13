# Container image for the tracker MCP server.
#
# The version reported by the `get_version` tool comes from the TRACKER_VERSION
# env var, set here from the VERSION build arg (the release workflow passes the
# release tag). Provide DATABASE_URI (and optionally NTFY_URL) at runtime. Run
# migrations separately: `docker run ... tracker-image alembic upgrade head`.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# The release tag, e.g. "0.3.0". Defaults to "dev" for local/CI builds that
# don't pass it, so `docker build .` still works.
ARG VERSION=dev
ENV TRACKER_VERSION=$VERSION

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Resolve and install dependencies first so this layer is cached until the
# lockfile changes, not on every source edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Install the project itself against the locked dependencies.
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

# Put the project's virtualenv on PATH so `tracker` / `alembic` are callable.
ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["tracker"]
