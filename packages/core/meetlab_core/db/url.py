"""Where agent-runner's database is.

DATABASE_URL wins (local dev, v1). Otherwise the URL is built from parts, which
is how ECS delivers it: the password comes from the RDS-managed secret, and
RDS-generated passwords can contain characters a hand-built URL would break on.
"""
from collections.abc import Mapping

from sqlalchemy.engine import URL

_PARTS = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")


def database_url(env: Mapping[str, str]) -> str:
    if env.get("DATABASE_URL"):
        return env["DATABASE_URL"]
    missing = [k for k in _PARTS if not env.get(k)]
    if missing:
        raise KeyError(f"set DATABASE_URL, or all of {', '.join(_PARTS)} (missing: {', '.join(missing)})")
    return URL.create(
        "postgresql+asyncpg",
        username=env["DB_USER"],
        password=env["DB_PASSWORD"],
        host=env["DB_HOST"],
        port=int(env.get("DB_PORT", "5432")),
        database=env["DB_NAME"],
        query={"ssl": "require"},  # RDS Postgres 17 forces TLS
    ).render_as_string(hide_password=False)


def pool_options(env: Mapping[str, str]) -> dict:
    """Connections one process may hold: SQLAlchemy's default unless set. A bot needs
    few (heartbeat, turn writes), and 100 bots on the default would exhaust the database."""
    return {"pool_size": int(env.get("DB_POOL_SIZE", "5")), "max_overflow": int(env.get("DB_MAX_OVERFLOW", "10"))}
