import traceback
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from livekit import api
from loguru import logger

from config import load_config, require
from runner_types import LiveKitRunnerArguments

config = load_config()
LIVEKIT_API_KEY = require(config.livekit_api_key, "LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = require(config.livekit_api_secret, "LIVEKIT_API_SECRET")
LIVEKIT_URL = require(config.livekit_url, "LIVEKIT_URL")

app = FastAPI(title="LiveKit Bot Runner")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _room_slug(room_name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in room_name.lower())
    slug = slug.strip("_")
    return slug[:24] or "room"


async def _create_bot_token(
    room_name: str,
    participant_identity: str,
    participant_name: str = "bot",
    agent_name: Optional[str] = None,
) -> str:
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(participant_identity)
        .with_name(participant_name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
                agent=True,
            )
        )
        .with_ttl(timedelta(minutes=15))
    )
    if agent_name:
        token = token.with_room_config(
            api.RoomConfiguration(agents=[api.AgentDispatch(agent_name=agent_name)])
        )
    return token.to_jwt()


@app.post("/start")
async def start_bot(request: Request, background_tasks: BackgroundTasks):
    try:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)

        if not isinstance(body, dict):
            return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)

        logger.debug(f"Received start request: {body}")

        room_name = body.get("room_name")
        if not isinstance(room_name, str) or not room_name.strip():
            return JSONResponse(
                {"error": "room_name is required and must be a non-empty string"},
                status_code=400,
            )
        room_name = room_name.strip()

        agent_name = None
        room_config = body.get("room_config")
        if isinstance(room_config, dict):
            agents = room_config.get("agents")
            if isinstance(agents, list) and agents and isinstance(agents[0], dict):
                maybe_agent_name = agents[0].get("agent_name")
                if isinstance(maybe_agent_name, str) and maybe_agent_name.strip():
                    agent_name = maybe_agent_name.strip()

        requested_bot_identity = body.get("bot_identity")
        if requested_bot_identity is not None:
            if not isinstance(requested_bot_identity, str) or not requested_bot_identity.strip():
                return JSONResponse(
                    {"error": "bot_identity must be a non-empty string"},
                    status_code=400,
                )
            bot_identity = requested_bot_identity.strip()
            if len(bot_identity) > 128:
                return JSONResponse(
                    {"error": "bot_identity must be 128 characters or fewer"},
                    status_code=400,
                )
        else:
            bot_identity = f"bot_{_room_slug(room_name)}_{uuid.uuid4().hex[:10]}"

        bot_token = await _create_bot_token(
            room_name=room_name,
            participant_identity=bot_identity,
            participant_name="Assistant",
            agent_name=agent_name,
        )

        runner_args = LiveKitRunnerArguments(
            url=LIVEKIT_URL,
            token=bot_token,
            room_name=room_name,
            body=body,
        )

        from bot import bot
        background_tasks.add_task(bot, runner_args)

        session_id = str(uuid.uuid4())
        logger.info(f"Starting bot session {session_id} in room {room_name}")

        return {
            "session_id": session_id,
            "room_name": room_name,
            "bot_identity": bot_identity,
            "message": "Bot is joining room",
        }

    except Exception as e:
        logger.error(f"Error starting bot: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "livekit-bot-runner"}


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting LiveKit Bot Runner — LiveKit URL: {LIVEKIT_URL}")
    uvicorn.run(app, host="0.0.0.0", port=7860)
