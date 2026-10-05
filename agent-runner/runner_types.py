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
    # A session that resumes one whose bot died (rejoin.load_resume): the conversation so far
    # as LLM messages, and the seconds since it began. None for a fresh session.
    resume: Optional[Dict[str, Any]] = None
