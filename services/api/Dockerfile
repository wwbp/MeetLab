# The API (python -m meetlab_api.runner), with packages/core: no Pipecat, no bot. Built from the
# repo root. A builder installs core's locked dependencies plus the `api` extra with uv into /venv;
# the runtime is plain Python with the venv and the code, found through PYTHONPATH. Its start
# command applies the database migrations first. Budget, entry points and "no Pipecat":
# `make test-image`.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/
WORKDIR /app
# The venv lives outside /app, so the dev stack can mount the repo over /app and still run
# production's exact environment.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/venv
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --extra api

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/venv/bin:$PATH" \
    PYTHONPATH=/app/packages/core:/app/services/api
COPY --from=build /venv /venv
COPY packages packages
COPY services/api services/api
EXPOSE 7860
CMD ["sh", "-c", "alembic -c packages/core/alembic.ini upgrade head && python -m meetlab_api.runner"]
