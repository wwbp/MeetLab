import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_BASE_ENV_PATH = Path(__file__).resolve().parent / ".env.runner"
_LOCAL_ENV_PATH = Path(__file__).resolve().parent / ".env.runner.local"

if _BASE_ENV_PATH.exists():
    load_dotenv(_BASE_ENV_PATH)
if _LOCAL_ENV_PATH.exists():
    load_dotenv(_LOCAL_ENV_PATH, override=True)


@dataclass(frozen=True)
class Config:
    openai_api_key: Optional[str]
    livekit_url: Optional[str]
    livekit_api_key: Optional[str]
    livekit_api_secret: Optional[str]


def load_config() -> Config:
    return Config(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        livekit_url=os.getenv("LIVEKIT_URL"),
        livekit_api_key=os.getenv("LIVEKIT_API_KEY"),
        livekit_api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )


def require(value: Optional[str], name: str) -> str:
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value
