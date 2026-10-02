"""The LiveKit token a bot joins with, minted by bot_task (one process per meeting)."""
from datetime import timedelta
from typing import Optional

from livekit import api


def bot_token(room_name: str, identity: str, key: str, secret: str, ttl_minutes: int,
              name: str = "bot", agent_name: Optional[str] = None) -> str:
    token = (
        api.AccessToken(key, secret)
        .with_identity(identity)
        .with_name(name)
        .with_grants(api.VideoGrants(
            room_join=True, room=room_name,
            can_publish=True, can_subscribe=True, can_publish_data=True, agent=True,
        ))
        .with_ttl(timedelta(minutes=ttl_minutes))
    )
    if agent_name:
        token = token.with_room_config(
            api.RoomConfiguration(agents=[api.AgentDispatch(agent_name=agent_name)])
        )
    return token.to_jwt()
