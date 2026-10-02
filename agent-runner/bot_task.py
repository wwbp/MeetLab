"""One bot, one process: `python -m bot_task --session-id <id>`.

The runner records the session and asks ECS (or Docker, locally) to start this; it
passes only the session ID. The process reads its row, mints its own LiveKit token,
runs the bot, and exits when the bot does, so the task's life is the meeting's.
"""
import argparse
import asyncio
import os
import sys

from loguru import logger

from bot_token import bot_token
from runner_types import LiveKitRunnerArguments


def parse_args(argv):
    p = argparse.ArgumentParser(prog="bot_task")
    p.add_argument("--session-id", required=True)
    return p.parse_args(argv)


def runner_args_for(row, url: str, token: str) -> LiveKitRunnerArguments:
    return LiveKitRunnerArguments(url=url, token=token, room_name=row.room_name,
                                  session_id=row.id, bot_identity=row.bot_identity,
                                  handle_sigterm=True)  # ECS StopTask -> graceful end


async def main(session_id: str) -> int:
    from config import load_config, require
    from db.engine import AsyncSessionLocal
    from db.models import Conversation

    async with AsyncSessionLocal() as s:
        row = await s.get(Conversation, session_id)
    if row is None:
        logger.error(f"bot_task: no session {session_id}")
        return 2
    if row.status != "running":
        # Stopped before this process started: Stop can land before ECS lists the
        # new task, so the runner had nothing to stop. The session row is the truth.
        logger.info(f"bot_task: session {session_id} is already {row.status}; not joining")
        return 0
    cfg = load_config()
    token = bot_token(
        row.room_name, row.bot_identity,
        key=require(cfg.livekit_api_key, "LIVEKIT_API_KEY"),
        secret=require(cfg.livekit_api_secret, "LIVEKIT_API_SECRET"),
        ttl_minutes=int(os.environ.get("BOT_TOKEN_TTL_MINUTES", "15")),
        name="Assistant", agent_name=(row.meta or {}).get("agent_name"),
    )
    from bot import bot  # heavy (pipecat, models): only once the session is known to exist

    logger.info(f"bot_task: session {session_id} in room {row.room_name}")
    await bot(runner_args_for(row, require(cfg.livekit_url, "LIVEKIT_URL"), token))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args(sys.argv[1:]).session_id)))
