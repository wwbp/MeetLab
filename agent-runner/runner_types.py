from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class LiveKitRunnerArguments:
    """Arguments passed to the bot from the runner."""

    url: str
    token: str
    room_name: str
    session_id: str
    bot_identity: str
    body: Optional[Dict[str, Any]] = None
