from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class LiveKitRunnerArguments:
    """Arguments passed to the bot from the runner.

    Attributes:
        url: The LiveKit server URL
        token: JWT token for authentication
        room_name: Name of the room to join
        body: Optional request body data with custom configuration
    """
    url: str
    token: str
    room_name: str
    body: Optional[Dict[str, Any]] = None
