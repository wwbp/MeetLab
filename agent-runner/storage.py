"""Unified file storage: local filesystem or S3.

Reads configuration from environment variables at call time so that tests can
patch os.environ without module-level caching.

    STORAGE_BACKEND         "local" (default) or "s3"
    RECORDINGS_PATH         local base directory (default /recordings)
    S3_KEY_ID               AWS access key id
    S3_KEY_SECRET           AWS secret access key
    S3_BUCKET               S3 bucket name
    S3_REGION               S3 region (e.g. us-east-1)
    S3_ENDPOINT             optional S3-compatible endpoint URL
    PRESIGNED_URL_TTL_SECONDS  presigned URL lifetime in seconds (default 3600)
"""

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


def _cfg() -> dict:
    return {
        "backend": os.environ.get("STORAGE_BACKEND", "local"),
        "path": os.environ.get("RECORDINGS_PATH", "/recordings"),
        "key_id": os.environ.get("S3_KEY_ID"),
        "key_secret": os.environ.get("S3_KEY_SECRET"),
        # LiveKit Cloud egress uploads from LiveKit's servers, so it needs a key of its
        # own; v2 sets a write-only one here and keeps the runner on its task role.
        # v1 has no EGRESS_ key and uses S3_KEY_* for both.
        "egress_key_id": os.environ.get("EGRESS_S3_KEY_ID") or os.environ.get("S3_KEY_ID"),
        "egress_key_secret": os.environ.get("EGRESS_S3_KEY_SECRET") or os.environ.get("S3_KEY_SECRET"),
        "bucket": os.environ.get("S3_BUCKET"),
        "region": os.environ.get("S3_REGION"),
        "endpoint": os.environ.get("S3_ENDPOINT") or None,
        "ttl": int(os.environ.get("PRESIGNED_URL_TTL_SECONDS", "3600")),
    }


def _require(cfg: dict, what: str) -> None:
    missing = [k for k in ("bucket", "region") if not cfg[k]]
    if missing:
        raise RuntimeError(f"{what} requires: {', '.join(missing)}")


def _s3(cfg: dict):
    """An S3 client. Explicit keys when S3_KEY_ID/S3_KEY_SECRET are set (v1, LiveKit
    egress); otherwise boto3's default chain, i.e. the ECS task's own role (v2)."""
    import boto3

    keys = ({"aws_access_key_id": cfg["key_id"], "aws_secret_access_key": cfg["key_secret"]}
            if cfg["key_id"] and cfg["key_secret"] else {})
    return boto3.client("s3", region_name=cfg["region"], **keys,
                        **({"endpoint_url": cfg["endpoint"]} if cfg["endpoint"] else {}))


def build_filename(room_name: str, file_type: str, ext: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in room_name)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{safe}-{file_type}.{ext}"


def build_recording_path(room_name: str) -> str:
    """Construct the local path or S3 key for a new recording file.

    For local storage this is the absolute path agent-runner will use.
    For S3 this is the key without the bucket prefix — passed to LiveKit egress.
    """
    cfg = _cfg()
    filename = build_filename(room_name, "recording", "mp4")
    if cfg["backend"] == "s3":
        return f"recordings/{filename}"
    return str(Path(cfg["path"]) / filename)


async def write_file(filename: str, content: bytes) -> str:
    """Write bytes to the configured backend. Returns the stored path/key."""
    cfg = _cfg()
    if cfg["backend"] == "s3":
        return await _upload_s3(filename, content, cfg)
    return _write_local(filename, content, cfg)


def _write_local(filename: str, content: bytes, cfg: dict) -> str:
    path = Path(cfg["path"]) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    logger.info(f"storage: wrote {len(content)} bytes → {path}")
    return str(path)


async def read_bytes(path: str) -> bytes:
    """Read a stored file's bytes from the configured backend (local path or S3 key)."""
    cfg = _cfg()
    if cfg["backend"] != "s3":
        return Path(path).read_bytes()

    def _sync_get() -> bytes:
        s3 = _s3(cfg)
        return s3.get_object(Bucket=cfg["bucket"], Key=path)["Body"].read()

    return await asyncio.get_event_loop().run_in_executor(None, _sync_get)


async def _upload_s3(filename: str, content: bytes, cfg: dict) -> str:
    _require(cfg, "S3 storage")
    key = f"recordings/{filename}"

    def _sync_upload() -> str:
        s3 = _s3(cfg)
        s3.put_object(Bucket=cfg["bucket"], Key=key, Body=content)
        logger.info(f"storage: uploaded {len(content)} bytes → s3://{cfg['bucket']}/{key}")
        return key

    return await asyncio.get_event_loop().run_in_executor(None, _sync_upload)


def get_download_url(path: str) -> str:
    """Return a URL for downloading the file.

    S3: generates a presigned GET URL valid for PRESIGNED_URL_TTL_SECONDS.
    Local: returns the path (agent-runner serves it directly via /media-files/{id}/download).
    """
    cfg = _cfg()
    if cfg["backend"] != "s3":
        return path

    _require(cfg, "S3 presign")
    s3 = _s3(cfg)
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": cfg["bucket"], "Key": path},
        ExpiresIn=cfg["ttl"],
    )
    logger.debug(f"storage: presigned URL for {path} (ttl={cfg['ttl']}s)")
    return url


def exists(path: str) -> bool:
    """Whether a stored file is there (the runner's recording reconcile asks)."""
    cfg = _cfg()
    if cfg["backend"] != "s3":
        return Path(path).exists()
    _require(cfg, "S3 exists")
    try:
        _s3(cfg).head_object(Bucket=cfg["bucket"], Key=path)
        return True
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def is_local() -> bool:
    return _cfg()["backend"] != "s3"


def local_abs_path(path: str) -> Path:
    """Resolve a stored local path to an absolute Path object."""
    return Path(path)
