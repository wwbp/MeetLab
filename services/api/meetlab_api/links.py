"""Group start links: stored links (/links endpoints), their passwords, legacy JWT links.

Step 0 holds the router (mounted by runner.py behind the runner's secret) and the helpers
the matchmaker uses. Request/reply shapes: tests/fixtures/matchmaking/*.json.
"""
import base64
import hashlib
import hmac
import os

from fastapi import APIRouter
from sqlalchemy.dialects.postgresql import insert as pg_insert

from meetlab_core.db.models import StartLink

router = APIRouter()

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


def hash_password(password: str) -> str:
    """scrypt$<salt b64>$<hash b64>: the only form a link's password is stored in."""
    salt = os.urandom(16)
    return "$".join(("scrypt", base64.b64encode(salt).decode(), base64.b64encode(_scrypt(password, salt)).decode()))


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        return hmac.compare_digest(_scrypt(password, base64.b64decode(salt, validate=True)),
                                   base64.b64decode(digest, validate=True))
    except ValueError:
        return False


async def ensure_legacy_link(db, link_id: str, pool: list[str]) -> None:
    """An old JWT link gets its record on first use (R30): size 1, delay 0, no password.
    A row already there is kept as it is (it may have been stopped)."""
    await db.execute(pg_insert(StartLink)
                     .values(link_id=link_id, name="Older link", pool=pool, legacy=True)
                     .on_conflict_do_nothing(index_elements=["link_id"]))
