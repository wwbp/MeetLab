import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# The repo root's .env (from .env.example), then .env.local; containers get theirs from compose
# or ECS, so these only matter when running on the host.
_ROOT = Path(__file__).resolve().parents[3]
_BASE_ENV_PATH = _ROOT / ".env"
_LOCAL_ENV_PATH = _ROOT / ".env.local"

if _BASE_ENV_PATH.exists():
    load_dotenv(_BASE_ENV_PATH)
if _LOCAL_ENV_PATH.exists():
    load_dotenv(_LOCAL_ENV_PATH, override=True)


@dataclass(frozen=True)
class Config:
    openai_api_key: Optional[str]
    elevenlabs_api_key: Optional[str]
    deepgram_api_key: Optional[str]
    livekit_url: Optional[str]
    livekit_api_key: Optional[str]
    livekit_api_secret: Optional[str]


def load_config() -> Config:
    return Config(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY"),
        deepgram_api_key=os.getenv("DEEPGRAM_API_KEY"),
        livekit_url=os.getenv("LIVEKIT_URL"),
        livekit_api_key=os.getenv("LIVEKIT_API_KEY"),
        livekit_api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )


def require(value: Optional[str], name: str) -> str:
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value
