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
from wtforms import SelectField
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

import storage
import transcript as transcript_mod
from config import load_config, require
from db.config_loader import load_bot_config
from db.engine import AsyncSessionLocal, engine
from db.models import BotConfig, Conversation, Event, MediaFile, Speaker, Utterance
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


class _NullableSelectField(SelectField):
    """SelectField that coerces empty string → None (for nullable DB columns)."""
    def process_formdata(self, valuelist):
        super().process_formdata(valuelist)
        if self.data == "":
            self.data = None


_LLM_CHOICES = [
    ("gpt-5.4-nano", "gpt-5.4-nano"),
    ("gpt-5.4-mini", "gpt-5.4-mini"),
    ("gpt-4.1-nano", "gpt-4.1-nano"),
    ("gpt-4.1-mini", "gpt-4.1-mini"),
    ("gpt-4o-mini", "gpt-4o-mini"),
]

_STT_MODEL_CHOICES = [
    ("nova-3-general", "nova-3-general (Deepgram)"),
    ("gpt-realtime-whisper", "gpt-realtime-whisper (OpenAI)"),
    ("gpt-4o-transcribe", "gpt-4o-transcribe (OpenAI)"),
    ("gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe (OpenAI)"),
]


class BotConfigAdmin(ModelView, model=BotConfig):
    column_list = [
        BotConfig.scope,
        BotConfig.stt_model,
        BotConfig.stt_vad_mode,
        BotConfig.llm_model,
        BotConfig.tts_provider,
        BotConfig.tts_voice,
        BotConfig.vad_stop_secs,
        BotConfig.updated_at,
    ]
    form_overrides = {
        "llm_model": SelectField,
        "stt_model": SelectField,
        "stt_vad_mode": SelectField,
        "stt_delay": _NullableSelectField,
        "tts_provider": SelectField,
    }
    form_args = {
        "llm_model": {"choices": _LLM_CHOICES},
        "stt_model": {"choices": _STT_MODEL_CHOICES},
        "stt_vad_mode": {"choices": [("local", "local")]},
        "stt_delay": {"choices": [("", "— (none)")]},
        "tts_provider": {"choices": [("elevenlabs", "elevenlabs"), ("openai", "openai")]},
    }
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

    # Flip pending recording MediaFile to available when egress finishes
    if event_type.strip().lower() in ("egress_ended", "egress_updated"):
        await _handle_egress_event(body)

    return {"status": "accepted"}


async def _handle_egress_event(body: dict) -> None:
    """Update the matching recording MediaFile when LiveKit egress finishes."""
    payload = body.get("payload") or {}
    egress_info = payload.get("egressInfo") or payload.get("egress_info") or {}
    egress_id = egress_info.get("egressId") or egress_info.get("egress_id")
    raw_status = egress_info.get("status", 0)
    # LiveKit EgressStatus: EGRESS_ENDING=2, EGRESS_COMPLETE=3, EGRESS_FAILED=4
    if not egress_id:
        return
    if raw_status not in (2, 3, 4):
        return
    new_status = "available" if raw_status in (2, 3) else "failed"
    try:
        # Read phase — own session so SELECT doesn't auto-begin a conflicting tx
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(MediaFile).where(
                    MediaFile.meta["egress_id"].astext == egress_id,
                    MediaFile.type == "recording",
                )
            )
            mf = result.scalar_one_or_none()

        if mf and mf.status == "pending":
            # Write phase — fresh session with explicit transaction
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    await db.execute(
                        update(MediaFile)
                        .where(MediaFile.id == mf.id)
                        .values(status=new_status)
                    )
            logger.info(f"egress {egress_id}: recording MediaFile {mf.id} → {new_status}")
    except Exception as exc:
        logger.warning(f"_handle_egress_event failed for {egress_id}: {exc}")


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
        "stt_delay": cfg.stt_delay,
    }


@app.put("/config")
async def update_config(request: Request, _=Depends(verify_api_key)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)

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
    if "stt_model" in body:
        _valid_stt = {"nova-3-general", "gpt-realtime-whisper", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"}
        if body["stt_model"] not in _valid_stt:
            return JSONResponse({"error": f"stt_model must be one of: {', '.join(sorted(_valid_stt))}"}, status_code=400)
        fields["stt_model"] = body["stt_model"]
    if "stt_vad_mode" in body:
        if body["stt_vad_mode"] not in ("local",):
            return JSONResponse({"error": "stt_vad_mode must be 'local'"}, status_code=400)
        fields["stt_vad_mode"] = body["stt_vad_mode"]
    if "stt_delay" in body:
        v = body["stt_delay"]
        if v is not None and v not in ("minimal", "low", "medium", "high", "xhigh"):
            return JSONResponse({"error": "stt_delay must be one of: minimal, low, medium, high, xhigh, or null"}, status_code=400)
        fields["stt_delay"] = v

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
        "tts_provider": cfg.tts_provider,
        "stt_model": cfg.stt_model,
        "stt_vad_mode": cfg.stt_vad_mode,
        "stt_delay": cfg.stt_delay,
    }


# ── helpers ──────────────────────────────────────────────────────────────────

def _lk_http_url() -> str:
    """Convert ws(s):// LiveKit URL to http(s):// for API calls."""
    return LIVEKIT_URL.replace("wss://", "https://").replace("ws://", "http://")


def _media_file_json(mf: MediaFile) -> dict:
    return {
        "id": mf.id,
        "conv_id": mf.conv_id,
        "type": mf.type,
        "status": mf.status,
        "path": mf.path,
        "created_at": mf.created_at.isoformat(),
        "meta": mf.meta,
    }


def _conversation_json(conv: Conversation, utterance_count: int) -> dict:
    return {
        "id": conv.id,
        "room_name": conv.room_name,
        "bot_identity": conv.bot_identity,
        "started_at": conv.started_at.isoformat(),
        "ended_at": conv.ended_at.isoformat() if conv.ended_at else None,
        "status": conv.status,
        "utterance_count": utterance_count,
        "media_files": [_media_file_json(mf) for mf in conv.media_files],
    }


# ── recording endpoints ───────────────────────────────────────────────────────

@app.post("/recordings/start")
async def start_recording(request: Request, _=Depends(verify_api_key)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)
    room_name = body.get("room_name")
    if not isinstance(room_name, str) or not room_name.strip():
        return JSONResponse({"error": "room_name is required"}, status_code=400)
    room_name = room_name.strip()

    # Look up current conversation for this room
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Conversation)
            .where(Conversation.room_name == room_name, Conversation.status == "running")
            .order_by(Conversation.started_at.desc())
            .limit(1)
        )
        conv = result.scalar_one_or_none()
        if not conv:
            return JSONResponse({"error": "no active session for this room"}, status_code=404)

        # Reject if a recording is already pending/available for this session
        result = await db.execute(
            select(MediaFile).where(
                MediaFile.conv_id == conv.id,
                MediaFile.type == "recording",
                MediaFile.status.in_(["pending", "available"]),
            )
        )
        if result.scalar_one_or_none():
            return JSONResponse({"error": "recording already active for this session"}, status_code=409)

    filepath = storage.build_recording_path(room_name)
    filename = filepath.split("/")[-1]

    from livekit.protocol.egress import (
        EncodedFileOutput,
        ListEgressRequest,
        RoomCompositeEgressRequest,
        S3Upload,
    )
    cfg = storage._cfg()

    if cfg["backend"] == "s3":
        missing = [k for k in ("key_id", "key_secret", "bucket", "region") if not cfg[k]]
        if missing:
            return JSONResponse({"error": f"S3 not fully configured: {missing}"}, status_code=500)
        file_output = EncodedFileOutput(
            filepath=f"recordings/{filename}",
            s3=S3Upload(
                access_key=cfg["key_id"],
                secret=cfg["key_secret"],
                bucket=cfg["bucket"],
                region=cfg["region"],
                **({"endpoint": cfg["endpoint"]} if cfg["endpoint"] else {}),
            ),
        )
    else:
        file_output = EncodedFileOutput(filepath=filepath)

    async with api.LiveKitAPI(
        url=_lk_http_url(), api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET
    ) as lk:
        # Check for active egress
        existing = await lk.egress.list_egress(ListEgressRequest(room_name=room_name))
        active = [e for e in existing.items if e.status < 2]
        if active:
            return JSONResponse({"error": "room already has an active recording egress"}, status_code=409)

        egress_info = await lk.egress.start_room_composite_egress(
            RoomCompositeEgressRequest(
                room_name=room_name,
                layout="speaker",
                file=file_output,
            )
        )
        egress_id = egress_info.egress_id

    media_file_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        async with db.begin():
            db.add(MediaFile(
                id=media_file_id,
                conv_id=conv.id,
                type="recording",
                status="pending",
                path=filepath,
                meta={"egress_id": egress_id, "filename": filename},
            ))

    logger.info(f"recording started: room={room_name} egress={egress_id} file={filepath}")
    return {"media_file_id": media_file_id, "egress_id": egress_id, "path": filepath}


@app.post("/recordings/stop")
async def stop_recording(request: Request, _=Depends(verify_api_key)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)
    room_name = body.get("room_name")
    if not isinstance(room_name, str) or not room_name.strip():
        return JSONResponse({"error": "room_name is required"}, status_code=400)
    room_name = room_name.strip()

    from livekit.protocol.egress import ListEgressRequest, StopEgressRequest

    async with api.LiveKitAPI(
        url=_lk_http_url(), api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET
    ) as lk:
        existing = await lk.egress.list_egress(ListEgressRequest(room_name=room_name))
        active = [e for e in existing.items if e.status < 2]
        if not active:
            return JSONResponse({"error": "no active recording found"}, status_code=404)
        for e in active:
            await lk.egress.stop_egress(StopEgressRequest(egress_id=e.egress_id))

    logger.info(f"recording stopped: room={room_name} egress_count={len(active)}")
    return {"stopped": len(active)}


# ── media file endpoints ───────────────────────────────────────────────────────

@app.patch("/media-files/{file_id}")
async def update_media_file(file_id: str, request: Request, _=Depends(verify_api_key)):
    """Update a MediaFile record — called by the meet webhook on egress_ended."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)

    allowed = {"status", "path", "meta"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if not fields:
        return JSONResponse({"error": "no updatable fields provided"}, status_code=400)

    async with AsyncSessionLocal() as db:
        async with db.begin():
            result = await db.execute(select(MediaFile).where(MediaFile.id == file_id))
            mf = result.scalar_one_or_none()
            if not mf:
                return JSONResponse({"error": "media file not found"}, status_code=404)
            await db.execute(
                update(MediaFile).where(MediaFile.id == file_id).values(**fields)
            )

    return {"updated": file_id}


@app.get("/media-files/{file_id}/download")
async def download_media_file(file_id: str, _=Depends(verify_api_key)):
    """Serve or redirect to a stored file.

    S3: returns 302 to a presigned URL.
    Local: streams the file directly.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(MediaFile).where(MediaFile.id == file_id))
        mf = result.scalar_one_or_none()

    if not mf:
        return JSONResponse({"error": "media file not found"}, status_code=404)
    if mf.status != "available":
        return JSONResponse({"error": f"file not ready (status={mf.status})"}, status_code=409)
    if not mf.path:
        return JSONResponse({"error": "file path not recorded"}, status_code=500)

    if not storage.is_local():
        url = storage.get_download_url(mf.path)
        return RedirectResponse(url=url, status_code=302)

    file_path = storage.local_abs_path(mf.path)
    if not file_path.exists():
        return JSONResponse({"error": "file not found on disk"}, status_code=404)

    ext = file_path.suffix.lower()
    media_types = {".mp4": "video/mp4", ".md": "text/markdown", ".txt": "text/plain"}
    media_type = media_types.get(ext, "application/octet-stream")
    return FileResponse(path=str(file_path), media_type=media_type, filename=file_path.name)


# ── conversations / meetings endpoints ────────────────────────────────────────

@app.get("/conversations")
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    _=Depends(verify_api_key),
):
    """List all conversations with utterance counts and media files, newest first."""
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)

    async with AsyncSessionLocal() as db:
        # Count utterances per conversation in one query
        count_rows = await db.execute(
            select(Utterance.conv_id, func.count(Utterance.id).label("cnt"))
            .group_by(Utterance.conv_id)
        )
        utt_counts: dict[str, int] = {row.conv_id: row.cnt for row in count_rows}

        result = await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.media_files))
            .order_by(Conversation.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        convs = list(result.scalars().all())

        # Total count for pagination
        total_result = await db.execute(select(func.count(Conversation.id)))
        total = total_result.scalar_one()

    return {
        "conversations": [
            _conversation_json(c, utt_counts.get(c.id, 0)) for c in convs
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.post("/conversations/{conv_id}/transcript")
async def queue_transcript(
    conv_id: str,
    background_tasks: BackgroundTasks,
    _=Depends(verify_api_key),
):
    """Queue async transcript generation. Returns immediately with media_file_id."""
    # Single transaction: check existence + insert atomically to avoid races
    media_file_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                select(Conversation).where(Conversation.id == conv_id)
            )
            conv = result.scalar_one_or_none()
            if not conv:
                return JSONResponse({"error": "conversation not found"}, status_code=404)

            result = await db.execute(
                select(MediaFile).where(
                    MediaFile.conv_id == conv_id,
                    MediaFile.type == "transcript",
                    MediaFile.status.in_(["pending", "available"]),
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                return JSONResponse(
                    {"error": "transcript already exists", "media_file_id": existing.id},
                    status_code=409,
                )

            db.add(MediaFile(
                id=media_file_id,
                conv_id=conv_id,
                type="transcript",
                status="pending",
            ))

    background_tasks.add_task(transcript_mod.generate_async, conv_id, media_file_id)
    logger.info(f"transcript queued: conv={conv_id} file={media_file_id}")
    return {"media_file_id": media_file_id, "status": "pending"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "livekit-bot-runner"}


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting LiveKit Bot Runner — LiveKit URL: {LIVEKIT_URL}")
    uvicorn.run(app, host="0.0.0.0", port=7860)
