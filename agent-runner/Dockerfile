# The runner and every bot (one image, two commands: runner.py; python -m bot_task, set by
# dispatch.py). Built once in a builder with uv; the runtime is plain Python with the venv and
# the code, nothing else (no ffmpeg: nothing calls it; soundfile bundles libsndfile). Budget and
# an import check of every entry point: `make test-image`.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/
WORKDIR /app
# The venv lives outside /app, so the dev stack can mount the code over /app and still run
# production's exact environment.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/venv
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/venv/bin:$PATH"
COPY --from=build /venv /venv
COPY . .
EXPOSE 7860
CMD ["sh", "-c", "alembic upgrade head && python runner.py"]
