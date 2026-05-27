import os
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from livekit import api
from loguru import logger
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import load_config, require
from db.config_loader import load_bot_config
from db.engine import AsyncSessionLocal, engine
from db.models import BotConfig, Conversation, Event, Speaker, Utterance
from runner_types import LiveKitRunnerArguments

config = load_config()

if config.enable_tracing:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from pipecat.utils.tracing.setup import setup_tracing

    _headers: dict[str, str] = {}
    if config.otlp_headers:
        for pair in config.otlp_headers.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                _headers[k.strip()] = v.strip()

    # OTLPSpanExporter uses the endpoint verbatim (no path appending) when
    # passed explicitly, so we must include the full OTLP traces path.
    _raw_endpoint = (config.otlp_endpoint or "http://jaeger:4318").rstrip("/")
    _exporter = OTLPSpanExporter(
        endpoint=_raw_endpoint + "/v1/traces",
        headers=_headers or None,
    )
    setup_tracing(
        service_name="meetlab-agent-runner",
        exporter=_exporter,
        console_export=config.otel_console_export,
    )
    logger.info(f"OTel tracing enabled → {config.otlp_endpoint or 'http://jaeger:4318'}")
LIVEKIT_API_KEY = require(config.livekit_api_key, "LIVEKIT_API_KEY")
BOT_RUNNER_SECRET = os.environ.get("BOT_RUNNER_SECRET")
LIVEKIT_API_SECRET = require(config.livekit_api_secret, "LIVEKIT_API_SECRET")
LIVEKIT_URL = require(config.livekit_url, "LIVEKIT_URL")


def verify_api_key(request: Request):
    if BOT_RUNNER_SECRET:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {BOT_RUNNER_SECRET}":
            raise HTTPException(status_code=401, detail="Unauthorized")


CONSOLE_PASSWORD = os.environ.get("CONSOLE_PASSWORD")


class AdminAuth(AuthenticationBackend):
    async def login(self, request: StarletteRequest) -> bool:
        form = await request.form()
        if CONSOLE_PASSWORD and form.get("password") == CONSOLE_PASSWORD:
            request.session["admin_authenticated"] = True
            return True
        return False

    async def logout(self, request: StarletteRequest) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: StarletteRequest) -> bool:
        return bool(request.session.get("admin_authenticated"))


class ProxyHeadersMiddleware(BaseHTTPMiddleware):
    """Rewrite ASGI scope scheme from CloudFront-Forwarded-Proto so SQLAdmin generates https:// URLs."""

    async def dispatch(self, request: Request, call_next):
        # CloudFront sends CloudFront-Forwarded-Proto (not X-Forwarded-Proto) to the origin.
        proto = (
            request.headers.get("cloudfront-forwarded-proto")
            or request.headers.get("x-forwarded-proto")
        )
        if proto:
            request.scope["scheme"] = proto.split(",")[0].strip()
        return await call_next(request)


app = FastAPI(title="LiveKit Bot Runner")
app.add_middleware(ProxyHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# SessionMiddleware is required for SQLAdmin CSRF tokens on create/edit forms.
app.add_middleware(SessionMiddleware, secret_key=LIVEKIT_API_SECRET)


# --- SQLAdmin ---

admin = Admin(app, engine, title="MeetLab Admin", base_url="/api/db", authentication_backend=AdminAuth(secret_key=LIVEKIT_API_SECRET))


class SpeakerAdmin(ModelView, model=Speaker):
    column_list = [Speaker.id, Speaker.meta]
    name = "Speaker"
    name_plural = "Speakers"


class ConversationAdmin(ModelView, model=Conversation):
    column_list = [
        Conversation.id,
        Conversation.room_name,
        Conversation.bot_identity,
        Conversation.status,
        Conversation.started_at,
        Conversation.ended_at,
    ]
    column_searchable_list = [Conversation.room_name]
    column_sortable_list = [Conversation.started_at, Conversation.status]
    name = "Conversation"
    name_plural = "Conversations"


class UtteranceAdmin(ModelView, model=Utterance):
    column_list = [
        Utterance.id,
        Utterance.conv_id,
        Utterance.speaker_id,
        Utterance.text,
        Utterance.ts,
        Utterance.meta,
    ]
    column_searchable_list = [Utterance.text, Utterance.conv_id]
    column_sortable_list = [Utterance.ts]
    name = "Utterance"
    name_plural = "Utterances"


class EventAdmin(ModelView, model=Event):
    column_list = [
        Event.id,
        Event.type,
        Event.room_name,
        Event.conv_id,
        Event.payload,
        Event.created_at,
    ]
    column_searchable_list = [Event.type, Event.room_name]
    column_sortable_list = [Event.created_at]
    can_create = False
    can_edit = False
    can_delete = False
    name = "Event"
    name_plural = "Events"


class BotConfigAdmin(ModelView, model=BotConfig):
    column_list = [
        BotConfig.scope,
        BotConfig.system_prompt,
        BotConfig.greeting,
        BotConfig.vad_stop_secs,
        BotConfig.llm_model,
        BotConfig.tts_voice,
        BotConfig.updated_at,
    ]
    name = "Bot Config"
    name_plural = "Bot Configs"


admin.add_view(SpeakerAdmin)
admin.add_view(ConversationAdmin)
admin.add_view(UtteranceAdmin)
admin.add_view(EventAdmin)
admin.add_view(BotConfigAdmin)


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
async def start_bot(request: Request, background_tasks: BackgroundTasks, _=Depends(verify_api_key)):
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

        session_id = str(uuid.uuid4())

        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(Speaker)
                    .values(id=bot_identity, meta={"role": "bot"})
                    .on_conflict_do_nothing(index_elements=["id"])
                )
                session.add(
                    Conversation(
                        id=session_id,
                        room_name=room_name,
                        bot_identity=bot_identity,
                        status="running",
                    )
                )

        runner_args = LiveKitRunnerArguments(
            url=LIVEKIT_URL,
            token=bot_token,
            room_name=room_name,
            session_id=session_id,
            bot_identity=bot_identity,
            body=body,
        )

        from bot import bot
        background_tasks.add_task(bot, runner_args)

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


@app.post("/events", status_code=202)
async def log_event(request: Request, _=Depends(verify_api_key)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)

    event_type = body.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return JSONResponse({"error": "type is required"}, status_code=400)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            session.add(
                Event(
                    type=event_type.strip(),
                    room_name=body.get("room_name"),
                    conv_id=body.get("conv_id"),
                    payload={k: v for k, v in body.items() if k not in ("type", "room_name", "conv_id")},
                )
            )
    return {"status": "accepted"}


@app.get("/config")
async def get_config(room: str | None = None, _=Depends(verify_api_key)):
    cfg = await load_bot_config(room)
    return {
        "scope": room or "global",
        "system_prompt": cfg.system_prompt,
        "greeting": cfg.greeting,
        "vad_stop_secs": cfg.vad_stop_secs,
        "llm_model": cfg.llm_model,
        "tts_voice": cfg.tts_voice,
        "tts_provider": cfg.tts_provider,
        "stt_model": cfg.stt_model,
        "stt_vad_mode": cfg.stt_vad_mode,
    }


@app.put("/config")
async def update_config(request: Request, _=Depends(verify_api_key)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)

    scope = body.get("scope", "global")
    if not isinstance(scope, str) or not scope.strip():
        return JSONResponse({"error": "scope must be a non-empty string"}, status_code=400)
    scope = scope.strip()

    fields: Dict[str, Any] = {}
    if "system_prompt" in body:
        if not isinstance(body["system_prompt"], str):
            return JSONResponse({"error": "system_prompt must be a string"}, status_code=400)
        fields["system_prompt"] = body["system_prompt"]
    if "greeting" in body:
        if not isinstance(body["greeting"], str):
            return JSONResponse({"error": "greeting must be a string"}, status_code=400)
        fields["greeting"] = body["greeting"]
    if "vad_stop_secs" in body:
        if not isinstance(body["vad_stop_secs"], (int, float)):
            return JSONResponse({"error": "vad_stop_secs must be a number"}, status_code=400)
        fields["vad_stop_secs"] = float(body["vad_stop_secs"])
    if "llm_model" in body:
        if not isinstance(body["llm_model"], str) or not body["llm_model"].strip():
            return JSONResponse({"error": "llm_model must be a non-empty string"}, status_code=400)
        fields["llm_model"] = body["llm_model"].strip()
    if "tts_voice" in body:
        if not isinstance(body["tts_voice"], str) or not body["tts_voice"].strip():
            return JSONResponse({"error": "tts_voice must be a non-empty string"}, status_code=400)
        fields["tts_voice"] = body["tts_voice"].strip()
    if "tts_provider" in body:
        if body["tts_provider"] not in ("elevenlabs", "openai"):
            return JSONResponse({"error": "tts_provider must be 'elevenlabs' or 'openai'"}, status_code=400)
        fields["tts_provider"] = body["tts_provider"]

    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(select(BotConfig).where(BotConfig.scope == scope))
            row = result.scalar_one_or_none()
            if row is None:
                row = BotConfig(scope=scope, **fields)
                session.add(row)
            else:
                for k, v in fields.items():
                    setattr(row, k, v)
                row.updated_at = datetime.now(timezone.utc)

    cfg = await load_bot_config(scope if scope != "global" else None)
    return {
        "scope": scope,
        "system_prompt": cfg.system_prompt,
        "greeting": cfg.greeting,
        "vad_stop_secs": cfg.vad_stop_secs,
        "llm_model": cfg.llm_model,
        "tts_voice": cfg.tts_voice,
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "livekit-bot-runner"}


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting LiveKit Bot Runner — LiveKit URL: {LIVEKIT_URL}")
    uvicorn.run(app, host="0.0.0.0", port=7860)
