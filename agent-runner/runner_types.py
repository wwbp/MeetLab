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
    # True only in a process of its own (bot_task): SIGTERM then cancels the pipeline
    # gracefully. In-process bots must leave the API process's signals alone.
    handle_sigterm: bool = False
