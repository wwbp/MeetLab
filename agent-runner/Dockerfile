FROM python:3.12-slim AS base

# Install system deps for audio processing (ffmpeg) and pipecat audio stack (libsndfile1)
RUN apt-get update && apt-get install -y ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*

# Bring in uv (fast Python installer) without leaving it in a builder image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Faster starts + saner linking when using cache mounts.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# 1) Pre-install deps in a separate layer for better caching.
#    Requires BuildKit: DOCKER_BUILDKIT=1
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=/app/uv.lock \
    --mount=type=bind,source=pyproject.toml,target=/app/pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# 2) Copy app and install the project itself (frozen to lockfile).
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# If your runner exposes HTTP on 7860, keep this; otherwise adjust/remove.
EXPOSE 7860

# Start your runner (change if your entrypoint differs)
CMD ["uv", "run", "python", "runner.py"]
