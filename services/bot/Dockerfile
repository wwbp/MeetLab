# One meeting's bot (python -m meetlab_bot.bot_task, started by the API: an ECS task in AWS, a
# container locally), with packages/core, and the load-test tool (python -m
# meetlab_loadtest.load_run, the load generator), which needs the same audio and LiveKit
# libraries. Built from the repo root like the API's image: core + the `bot` extra in /venv, the
# code on PYTHONPATH. Budget and entry points: `make test-image`.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/venv
# EXTRAS: production builds the bot alone; the local `tests` container (compose) adds `api`, since
# the cross-service tests import both.
ARG EXTRAS="--extra bot"
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project $EXTRAS

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/venv/bin:$PATH" \
    PYTHONPATH=/app/packages/core:/app/services/bot:/app/tools/loadtest
COPY --from=build /venv /venv
COPY packages packages
COPY services/bot services/bot
COPY tools/loadtest tools/loadtest
CMD ["python", "-m", "meetlab_bot.bot_task"]
