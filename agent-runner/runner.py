import asyncio
import io
import os
import socket
import threading
import traceback
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

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

from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

import capacity
import metrics
import sessions
import storage
import transcript as transcript_mod
from config import load_config, require
from db.config_loader import load_bot_config
from event_log import EVENT_SEVERITIES, install_error_event_sink, record_event
from db.engine import AsyncSessionLocal, engine
from db.models import BotConfig, Conversation, Event, MediaFile, Speaker, Utterance
from dispatch import (
    DispatchError,
    DockerApi,
    EcsBotTarget,
    run_bot_container,
    run_bot_task,
    stop_bot_container,
    stop_bot_task,
)
from heartbeat import fail_silent_sessions, request_recording

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

    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry import metrics as otel_metrics

    _metric_exporter = OTLPMetricExporter(
        endpoint=_raw_endpoint + "/v1/metrics",
        headers=_headers or None,
    )
    _metric_reader = PeriodicExportingMetricReader(
        _metric_exporter, export_interval_millis=15_000
    )
    _latency_agg = ExplicitBucketHistogramAggregation(metrics.LATENCY_BOUNDARIES)
    _meter_provider = MeterProvider(
        metric_readers=[_metric_reader],
        views=[
            View(instrument_name="meetlab.e2e_latency_ms", aggregation=_latency_agg),
            View(instrument_name="meetlab.llm_ttft_ms", aggregation=_latency_agg),
            View(instrument_name="meetlab.sentence_agg_ms", aggregation=_latency_agg),
            View(instrument_name="meetlab.tts_ttfb_ms", aggregation=_latency_agg),
        ],
    )
    otel_metrics.set_meter_provider(_meter_provider)
    logger.info(f"OTel metrics enabled → {_raw_endpoint}/v1/metrics")
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
        Event.created_at,
        Event.severity,
        Event.type,
        Event.room_name,
        Event.conv_id,
        Event.payload,
    ]
    column_searchable_list = [Event.type, Event.room_name, Event.severity]
    column_sortable_list = [Event.created_at, Event.severity]
    column_default_sort = [(Event.created_at, True)]
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
    ("Qwen/Qwen2.5-7B-Instruct", "Qwen2.5-7B-Instruct (ours)"),
]

_STT_MODEL_CHOICES = [
    ("parakeet-tdt-0.6b-v2", "parakeet-tdt-0.6b-v2 (Parakeet NIM) (default)"),
    ("parakeet-unified-en-0.6b", "parakeet-unified-en-0.6b (Parakeet NIM, offline)"),
    ("nova-3-general", "nova-3-general (Deepgram)"),
    ("gpt-realtime-whisper", "gpt-realtime-whisper (OpenAI)"),
    ("gpt-4o-transcribe", "gpt-4o-transcribe (OpenAI)"),
    ("gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe (OpenAI)"),
    ("whisper-turbo", "whisper-turbo (local faster-whisper, CPU)"),
    ("whisper-base", "whisper-base (local faster-whisper, CPU)"),
    ("whisper-small", "whisper-small (local faster-whisper, CPU)"),
]


class BotConfigAdmin(ModelView, model=BotConfig):
    column_list = [
        BotConfig.scope,
        BotConfig.stt_model,
        BotConfig.stt_vad_mode,
        BotConfig.llm_model,
        BotConfig.tts_provider,
        BotConfig.tts_voice,
        BotConfig.stt_endpointing_ms,
        BotConfig.user_speech_timeout_ms,
        BotConfig.turn_detection,
        BotConfig.smart_turn_wait_ms,
        BotConfig.session_limit_minutes,
        BotConfig.updated_at,
    ]
    form_overrides = {
        "llm_model": SelectField,
        "stt_model": SelectField,
        "stt_vad_mode": SelectField,
        "stt_delay": _NullableSelectField,
        "tts_provider": SelectField,
        "tts_aggregation_mode": SelectField,
        "turn_detection": SelectField,
        "stt_endpointing_ms": SelectField,
        "user_speech_timeout_ms": SelectField,
    }
    form_args = {
        "llm_model": {"choices": _LLM_CHOICES},
        "stt_model": {"choices": _STT_MODEL_CHOICES},
        "stt_vad_mode": {"choices": [("local", "local")]},
        "stt_delay": {"choices": [("", "— (none)")]},
        "tts_provider": {"choices": [("elevenlabs", "elevenlabs"), ("openai", "openai"), ("kokoro", "kokoro (ours)")]},
        "tts_aggregation_mode": {"choices": [("sentence", "sentence (default)"), ("token", "token (lower latency)")]},
        "turn_detection": {"choices": [("silence", "after a silence (default)"), ("smart_turn", "when the speaker sounds finished (smart turn)")]},
        # These two ADD together to form the turn-end window; 450+300=750ms is
        # calibrated against real pilot audio (tests/test_turn_calibration.py).
        # Judge any change by the sum. Choices are kept inside the range PUT
        # /config accepts, and a test asserts the current defaults appear here —
        # this list previously still said 100/200/50 after the default moved to
        # 450, so saving any row would have silently regressed it.
        "stt_endpointing_ms": {"choices": [
            ("200", "200ms (aggressive — cuts people off)"),
            ("300", "300ms"),
            ("450", "450ms (default, calibrated)"),
            ("600", "600ms"),
            ("800", "800ms (patient)"),
        ]},
        "user_speech_timeout_ms": {"choices": [
            ("150", "150ms (snappy)"),
            ("300", "300ms (default, calibrated)"),
            ("450", "450ms"),
            ("600", "600ms (patient — may merge turns)"),
        ]},
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


def _ecs_client():
    import boto3

    return boto3.client("ecs")


def _stop_bot(session_id: str) -> None:
    # ponytail: blocking boto3 call inside the reconcile loop; fine for a handful of
    # silent sessions per tick, move to asyncio.to_thread if that ever grows.
    if os.environ.get("BOT_DISPATCHER") == "ecs":
        stop_bot_task(_ecs_client(), session_id, EcsBotTarget.from_env(os.environ))
    elif os.environ.get("BOT_DISPATCHER") == "docker":
        # Docker's stop call blocks until the container exits (up to the 120 s grace);
        # ECS StopTask returns at once. Send it and don't wait, as on ECS: the SIGTERM
        # goes out immediately and the container exits on its own.
        def stop():
            try:
                stop_bot_container(_docker_api(), session_id)
            except Exception as e:
                logger.warning(f"could not stop the bot container for session {session_id}: {e}")
        threading.Thread(target=stop, daemon=True).start()


def _docker_api() -> DockerApi:
    return DockerApi(os.environ["DOCKER_HOST"])


@app.post("/start")
async def start_bot(request: Request, _=Depends(verify_api_key)):
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

        # One task (ECS) or container (local Docker) per meeting (4c); there is no
        # in-process bot (removed in #113). Refuse before a session row exists, so a
        # misconfigured runner never leaves a 'running' session no bot will join.
        dispatcher = os.environ.get("BOT_DISPATCHER")
        if dispatcher not in ("ecs", "docker"):
            logger.error(f"BOT_DISPATCHER is {dispatcher!r}; cannot start a bot for {room_name}")
            return JSONResponse({"error": "BOT_DISPATCHER must be 'ecs' or 'docker'"}, status_code=500)

        session_id = str(uuid.uuid4())

        # Persist the caller's custom_data (e.g. requested_by, bot_config_scope from a
        # start link) on the conversation so "which config ran this session" is queryable.
        custom_data = body.get("custom_data")
        conv_meta = dict(custom_data) if isinstance(custom_data, dict) else {}
        if agent_name:
            conv_meta["agent_name"] = agent_name  # bot_task re-mints the same token from the row

        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(Speaker)
                    .values(id=bot_identity, meta={"role": "bot"})
                    .on_conflict_do_nothing(index_elements=["id"])
                )
                # One running session per room (uq_conversations_one_running_per_room):
                # a repeated start returns the session already running, no second bot.
                inserted = (await session.execute(
                    pg_insert(Conversation)
                    .values(id=session_id, room_name=room_name, bot_identity=bot_identity,
                            status="running", meta=conv_meta)
                    .on_conflict_do_nothing(index_elements=["room_name"],
                                            index_where=text("status = 'running'"))
                    .returning(Conversation.id)
                )).scalar_one_or_none()
                running = None if inserted else (await session.execute(
                    select(Conversation).where(Conversation.room_name == room_name,
                                               Conversation.status == "running")
                )).scalar_one()

        if running is not None:
            logger.info(f"Room {room_name} already has running session {running.id}; not starting another bot")
            return {
                "session_id": running.id,
                "room_name": room_name,
                "bot_identity": running.bot_identity,
                "message": "Bot already running in this room",
                "already_running": True,
            }

        # The bot reads this row and mints its own token; the session ID makes a retry
        # the same bot (ECS clientToken, Docker container name).
        try:
            if dispatcher == "ecs":
                task_arn = await asyncio.to_thread(
                    run_bot_task, _ecs_client(), session_id, EcsBotTarget.from_env(os.environ))
            else:
                task_arn = await asyncio.to_thread(
                    run_bot_container, _docker_api(), session_id, socket.gethostname())
        except DispatchError as e:
            async with AsyncSessionLocal() as session, session.begin():
                await sessions.end(session, [session_id], "dispatch_failed")
            logger.error(f"bot dispatch failed for {room_name}: {e}")
            return JSONResponse({"error": str(e), "session_id": session_id}, status_code=503)
        logger.info(f"Started bot {task_arn} for session {session_id}")
        if (await load_bot_config(room_name)).auto_record:
            _record_from_the_start(room_name)
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


_RECORDING_STARTS: set = set()  # keep each background start alive until it finishes


def _record_from_the_start(room_name: str) -> None:
    """Auto-record: the room recording starts with the session, before the bot joins,
    so its greeting is recorded. Here because only the runner holds the egress key."""
    async def start():
        status, payload = await start_recording_for_room(room_name)
        logger.info(f"auto_record: {room_name} recording → {status} {payload}")

    task = asyncio.create_task(start())
    _RECORDING_STARTS.add(task)
    task.add_done_callback(_RECORDING_STARTS.discard)


@app.post("/stop")
async def stop_bot(request: Request, _=Depends(verify_api_key)):
    """Stop the room's running bot: the console's Stop button.

    Removing the bot from LiveKit (meet does that too) does nothing before the bot
    has joined, and a starting bot then joined anyway. So stop it at its task
    (SIGTERM: a bot that is running ends gracefully, one still starting never runs)
    and close the session, so the room can start a new bot at once.
    """
    body = await request.json()
    room_name = (body.get("room_name") or "").strip() if isinstance(body, dict) else ""
    if not room_name:
        return JSONResponse({"error": "room_name is required"}, status_code=400)
    async with AsyncSessionLocal() as session:
        running = (await session.execute(
            select(Conversation).where(Conversation.room_name == room_name, Conversation.status == "running")
        )).scalar_one_or_none()
    if running is None:
        return {"stopped": None}
    await asyncio.to_thread(_stop_bot, running.id)
    async with AsyncSessionLocal() as session, session.begin():
        await sessions.end(session, [running.id], "stopped")
    logger.info(f"Stopped bot session {running.id} in room {room_name}")
    return {"stopped": running.id}


@app.get("/rooms/{room_name}/session")
async def room_session(room_name: str, _=Depends(verify_api_key)):
    """The room's running session, or null: meet's view of which bot a room has (iteration 9)."""
    async with AsyncSessionLocal() as db:
        running = (await db.execute(select(Conversation).where(
            Conversation.room_name == room_name, Conversation.status == sessions.RUNNING))).scalar_one_or_none()
    return {"session": {"session_id": running.id, "bot_identity": running.bot_identity,
                        "started_at": running.started_at.isoformat()} if running else None}


# --- Prepare for study: pre-warm the bot pool (capacity.py) -----------------------

def _asg_client():
    import boto3

    return boto3.client("autoscaling")


def _prewarm_target():
    """(autoscaling, ecs, cluster, group, sessions per instance), or None when bots aren't ECS tasks."""
    if os.environ.get("BOT_DISPATCHER") != "ecs":
        return None
    # ponytail: 3 bots per c6i.large (1 GB each in 4 GB); measure at load testing.
    return (_asg_client(), _ecs_client(), os.environ["ECS_CLUSTER"], os.environ["BOT_ASG_NAME"],
            int(os.environ.get("BOTS_PER_INSTANCE", "3")))


def _pool_baseline() -> int:
    """Bot machines kept warm at all times (Terraform's bot_pool_min)."""
    return int(os.environ.get("BOT_POOL_MIN", "0"))


_LOCAL_BOTS = "bots run as local containers here; there is no pool to warm"


@app.get("/capacity")
async def get_capacity(_=Depends(verify_api_key)):
    target = _prewarm_target()
    if target is None:
        return {"available": False, "reason": _LOCAL_BOTS}
    asg, ecs, cluster, group, per = target
    return {"available": True, **await asyncio.to_thread(capacity.status, asg, ecs, cluster, group, per)}


@app.post("/capacity/prewarm")
async def prewarm_capacity(request: Request, _=Depends(verify_api_key)):
    """Warm enough bot instances for `sessions` until `until` (ISO 8601 with offset)."""
    target = _prewarm_target()
    if target is None:
        return JSONResponse({"error": _LOCAL_BOTS}, status_code=409)
    body = await request.json()
    try:
        sessions = body["sessions"]
        until = datetime.fromisoformat(body["until"])
        if not isinstance(sessions, int) or isinstance(sessions, bool) or until.tzinfo is None:
            raise ValueError("sessions must be a whole number and until must carry a time zone")
        asg, ecs, cluster, group, per = target
        result = await asyncio.to_thread(capacity.prewarm, asg, group, sessions, until, per,
                                         datetime.now(timezone.utc), _pool_baseline())
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": f"bad request: {e}"}, status_code=400)
    logger.info(f"bot pool pre-warmed: {sessions} sessions, {result['instances']} instance(s) until {until.isoformat()}")
    return {**result, "available": True, **await asyncio.to_thread(capacity.status, asg, ecs, cluster, group, per)}


@app.delete("/capacity/prewarm")
async def cancel_prewarm(_=Depends(verify_api_key)):
    target = _prewarm_target()
    if target is None:
        return JSONResponse({"error": _LOCAL_BOTS}, status_code=409)
    asg, ecs, cluster, group, per = target
    await asyncio.to_thread(capacity.cancel, asg, group, _pool_baseline())
    logger.info("bot pool pre-warm cancelled")
    return {"available": True, **await asyncio.to_thread(capacity.status, asg, ecs, cluster, group, per)}


# Reads are for a human scrolling a console, not for bulk export.
EVENTS_MAX_LIMIT = 500
EVENTS_DEFAULT_LIMIT = 100


def _event_json(ev: Event) -> dict:
    return {
        "id": ev.id,
        "type": ev.type,
        "severity": getattr(ev, "severity", "info"),
        "room_name": ev.room_name,
        "conv_id": ev.conv_id,
        "payload": ev.payload or {},
        "created_at": ev.created_at.isoformat(),
    }


@app.get("/events")
async def list_events(
    severity: str | None = None,
    room: str | None = None,
    conv_id: str | None = None,
    type: str | None = None,
    limit: str | None = None,
    _=Depends(verify_api_key),
):
    """Newest-first event log, filterable — this is what the admin console reads."""
    if severity is not None and severity not in EVENT_SEVERITIES:
        return JSONResponse(
            {"error": f"severity must be one of: {', '.join(sorted(EVENT_SEVERITIES))}"},
            status_code=400,
        )

    try:
        bounded_limit = int(limit) if limit is not None else EVENTS_DEFAULT_LIMIT
    except (TypeError, ValueError):
        bounded_limit = EVENTS_DEFAULT_LIMIT
    bounded_limit = max(1, min(EVENTS_MAX_LIMIT, bounded_limit))

    stmt = select(Event).order_by(Event.created_at.desc(), Event.id.desc()).limit(bounded_limit)
    if severity:
        stmt = stmt.where(Event.severity == severity)
    if room:
        stmt = stmt.where(Event.room_name == room)
    if conv_id:
        stmt = stmt.where(Event.conv_id == conv_id)
    if type:
        stmt = stmt.where(Event.type == type)

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return {"events": [_event_json(ev) for ev in rows]}


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

    severity = body.get("severity", "info")
    if severity not in EVENT_SEVERITIES:
        return JSONResponse(
            {"error": f"severity must be one of: {', '.join(sorted(EVENT_SEVERITIES))}"},
            status_code=400,
        )

    await record_event(
        event_type=event_type.strip(),
        severity=severity,
        room_name=body.get("room_name"),
        conv_id=body.get("conv_id"),
        payload={
            k: v for k, v in body.items()
            if k not in ("type", "room_name", "conv_id", "severity")
        },
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


@app.get("/configs")
async def list_configs(_=Depends(verify_api_key)):
    """List all bot_config rows by scope — feeds the console's start-link pool picker.

    Scopes are free-text ('global', a room name, or an admin-chosen preset name like
    'friendly'); they're indistinguishable by design, so list everything and let the
    admin pick.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(BotConfig.scope, BotConfig.updated_at).order_by(BotConfig.scope)
        )
        rows = result.all()
    return {
        "configs": [
            {"scope": scope, "updated_at": updated_at.isoformat() if updated_at else None}
            for scope, updated_at in rows
        ]
    }


@app.get("/config")
async def get_config(room: str | None = None, _=Depends(verify_api_key)):
    cfg = await load_bot_config(room)
    return {
        "scope": room or "global",
        "system_prompt": cfg.system_prompt,
        "greeting": cfg.greeting,
        "llm_model": cfg.llm_model,
        "tts_voice": cfg.tts_voice,
        "tts_provider": cfg.tts_provider,
        "tts_aggregation_mode": cfg.tts_aggregation_mode,
        "turn_detection": cfg.turn_detection,
        "smart_turn_wait_ms": cfg.smart_turn_wait_ms,
        "stt_model": cfg.stt_model,
        "stt_vad_mode": cfg.stt_vad_mode,
        "stt_delay": cfg.stt_delay,
        "stt_endpointing_ms": cfg.stt_endpointing_ms,
        "user_speech_timeout_ms": cfg.user_speech_timeout_ms,
        "auto_record": cfg.auto_record,
        "session_limit_minutes": cfg.session_limit_minutes,
        "closing_message": cfg.closing_message,
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
    if "llm_model" in body:
        if not isinstance(body["llm_model"], str) or not body["llm_model"].strip():
            return JSONResponse({"error": "llm_model must be a non-empty string"}, status_code=400)
        fields["llm_model"] = body["llm_model"].strip()
    if "tts_voice" in body:
        if not isinstance(body["tts_voice"], str) or not body["tts_voice"].strip():
            return JSONResponse({"error": "tts_voice must be a non-empty string"}, status_code=400)
        fields["tts_voice"] = body["tts_voice"].strip()
    if "tts_provider" in body:
        if body["tts_provider"] not in ("elevenlabs", "openai", "kokoro"):
            return JSONResponse({"error": "tts_provider must be 'elevenlabs', 'openai' or 'kokoro'"}, status_code=400)
        fields["tts_provider"] = body["tts_provider"]
    if "stt_model" in body:
        _valid_stt = {
            "nova-3-general", "gpt-realtime-whisper", "gpt-4o-transcribe", "gpt-4o-mini-transcribe",
            # local faster-whisper (whisper-<model_size>); CPU-only — see Experiment 5
            "whisper-turbo", "whisper-base", "whisper-small",
            # Parakeet NIM (self-hosted GPU, NEMOTRON_STT_URL) — see Experiments 6-7.
            # parakeet-unified-en-0.6b is NVIDIA's offline+streaming unified English model.
            "parakeet-tdt-0.6b-v2",
            "parakeet-unified-en-0.6b",
        }
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
    if "smart_turn_wait_ms" in body:
        v = body["smart_turn_wait_ms"]
        if not isinstance(v, int) or isinstance(v, bool) or not 500 <= v <= 5000:
            return JSONResponse({"error": "smart_turn_wait_ms must be a whole number from 500 to 5000"}, status_code=400)
        fields["smart_turn_wait_ms"] = v
    if "turn_detection" in body:
        if body["turn_detection"] not in ("silence", "smart_turn"):
            return JSONResponse({"error": "turn_detection must be 'silence' or 'smart_turn'"}, status_code=400)
        fields["turn_detection"] = body["turn_detection"]
    if "tts_aggregation_mode" in body:
        if body["tts_aggregation_mode"] not in ("sentence", "token"):
            return JSONResponse({"error": "tts_aggregation_mode must be 'sentence' or 'token'"}, status_code=400)
        fields["tts_aggregation_mode"] = body["tts_aggregation_mode"]
    if "stt_endpointing_ms" in body:
        # A duration, so range-checked rather than enumerated. It was previously
        # an allow-list of (50, 100, 200), which silently became a landmine the
        # moment the default moved outside it: /api/start-link materialises a room
        # config by GETting a pool config and PUTting it back, so an unaccepted
        # default makes every start link fail with a 502. Bounds are wide enough
        # to tune within (see docs/pilot-postmortem-2026-08.md RC2) and tight
        # enough to catch nonsense.
        v = body["stt_endpointing_ms"]
        if isinstance(v, bool) or not isinstance(v, int) or not (50 <= v <= 2000):
            return JSONResponse(
                {"error": "stt_endpointing_ms must be an integer between 50 and 2000"},
                status_code=400,
            )
        fields["stt_endpointing_ms"] = int(v)
    if "user_speech_timeout_ms" in body:
        # Same shape as stt_endpointing_ms: a duration, range-checked rather than
        # enumerated, so moving the default never puts it outside its own validator.
        v = body["user_speech_timeout_ms"]
        if isinstance(v, bool) or not isinstance(v, int) or not (50 <= v <= 2000):
            return JSONResponse(
                {"error": "user_speech_timeout_ms must be an integer between 50 and 2000"},
                status_code=400,
            )
        fields["user_speech_timeout_ms"] = int(v)
    if "auto_record" in body:
        if not isinstance(body["auto_record"], bool):
            return JSONResponse({"error": "auto_record must be a boolean"}, status_code=400)
        fields["auto_record"] = body["auto_record"]
    if "session_limit_minutes" in body:
        v = body["session_limit_minutes"]
        # bool is a subclass of int in Python — reject it explicitly so True
        # can't be stored as a 1-minute limit.
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 1440:
            return JSONResponse(
                {"error": "session_limit_minutes must be an integer between 0 and 1440 (0 = unlimited)"},
                status_code=400,
            )
        fields["session_limit_minutes"] = v
    if "closing_message" in body:
        if not isinstance(body["closing_message"], str):
            return JSONResponse({"error": "closing_message must be a string"}, status_code=400)
        fields["closing_message"] = body["closing_message"]

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
        "llm_model": cfg.llm_model,
        "tts_voice": cfg.tts_voice,
        "tts_provider": cfg.tts_provider,
        "tts_aggregation_mode": cfg.tts_aggregation_mode,
        "turn_detection": cfg.turn_detection,
        "smart_turn_wait_ms": cfg.smart_turn_wait_ms,
        "stt_model": cfg.stt_model,
        "stt_vad_mode": cfg.stt_vad_mode,
        "stt_delay": cfg.stt_delay,
        "stt_endpointing_ms": cfg.stt_endpointing_ms,
        "user_speech_timeout_ms": cfg.user_speech_timeout_ms,
        "auto_record": cfg.auto_record,
        "session_limit_minutes": cfg.session_limit_minutes,
        "closing_message": cfg.closing_message,
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
        "meta": conv.meta or {},
        "media_files": [_media_file_json(mf) for mf in conv.media_files],
    }


async def _reconcile_pending_local_recordings() -> None:
    """Mark finished local recordings available if the webhook was missed."""
    if not storage.is_local():
        return

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(MediaFile)
                .join(Conversation)
                .where(
                    MediaFile.type == "recording",
                    MediaFile.status == "pending",
                    MediaFile.path.is_not(None),
                    Conversation.status != "running",
                )
            )
            pending = list(result.scalars().all())

        ready_ids: list[str] = []
        for mf in pending:
            if not mf.path:
                continue
            file_path = storage.local_abs_path(mf.path)
            if file_path.exists() and file_path.stat().st_size > 0:
                ready_ids.append(mf.id)

        if not ready_ids:
            return

        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    update(MediaFile)
                    .where(MediaFile.id.in_(ready_ids))
                    .values(status="available")
                )
        logger.info(f"reconciled {len(ready_ids)} pending local recording(s) to available")
    except Exception as exc:
        logger.warning(f"local recording reconciliation failed: {exc}")


def _is_missing_egress_output_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "invalid_argument" in msg and "missing or invalid field: output" in msg


class EgressOutputCompatibilityError(RuntimeError):
    def __init__(self, file_outputs_error: Exception, legacy_file_error: Exception):
        self.file_outputs_error = file_outputs_error
        self.legacy_file_error = legacy_file_error
        super().__init__(
            "LiveKit rejected both room composite egress output formats: "
            f"file_outputs={file_outputs_error}; legacy_file={legacy_file_error}"
        )


async def _start_room_composite_egress(lk, room_name: str, file_output):
    from livekit.protocol.egress import RoomCompositeEgressRequest

    try:
        return await lk.egress.start_room_composite_egress(
            RoomCompositeEgressRequest(
                room_name=room_name,
                layout="speaker",
                file_outputs=[file_output],
            )
        )
    except Exception as exc:
        if not _is_missing_egress_output_error(exc):
            raise
        logger.warning(
            "room composite egress rejected file_outputs; retrying with legacy file output"
        )
        try:
            return await lk.egress.start_room_composite_egress(
                RoomCompositeEgressRequest(
                    room_name=room_name,
                    layout="speaker",
                    file=file_output,
                )
            )
        except Exception as legacy_exc:
            raise EgressOutputCompatibilityError(exc, legacy_exc) from legacy_exc


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

    status, payload = await start_recording_for_room(room_name.strip())
    if status == 200:
        return payload
    return JSONResponse(payload, status_code=status)


async def start_recording_for_room(room_name: str) -> tuple[int, dict]:
    """Start composite egress + per-speaker WAV capture for a room.

    Returns (status_code, payload) so both the HTTP endpoint and the bot's
    auto-start path (on_first_participant_joined) can share one implementation:
    the endpoint maps the tuple to a JSONResponse; the bot logs on non-200.

    Per-speaker WAV capture is enabled as soon as a running session is confirmed —
    before composite egress — so it still records even if egress fails (e.g. no S3
    on LiveKit Cloud, or the room isn't in LiveKit yet).
    """
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
            return 404, {"error": "no active session for this room"}

        # Reject if a recording is already pending/available for this session
        result = await db.execute(
            select(MediaFile).where(
                MediaFile.conv_id == conv.id,
                MediaFile.type == "recording",
                MediaFile.status.in_(["pending", "available"]),
            )
        )
        already_recording = result.scalar_one_or_none() is not None

    # Turn on per-speaker WAV capture: the bot (its own task or container) reads the
    # flag on its next heartbeat. Idempotent, so a duplicate request is harmless.
    await request_recording(AsyncSessionLocal, conv.id)

    if already_recording:
        return 409, {"error": "recording already active for this session"}

    filepath = storage.build_recording_path(room_name)
    filename = filepath.split("/")[-1]

    from livekit.protocol.egress import (
        EncodedFileOutput,
        ListEgressRequest,
        S3Upload,
    )
    cfg = storage._cfg()

    # LiveKit Cloud egress runs on LiveKit's infrastructure and cannot write to
    # the local filesystem.  S3 (or another cloud backend) is required.
    # Self-hosted LiveKit with the egress container volume-mounted can use local.
    lk_url = _lk_http_url()
    is_cloud_livekit = "livekit.cloud" in lk_url or "livekit.io" in lk_url
    if is_cloud_livekit and cfg["backend"] != "s3":
        return 400, {
            "error": (
                "LiveKit Cloud requires S3 storage for recordings. "
                "Set STORAGE_BACKEND=s3 and configure EGRESS_S3_KEY_ID (or S3_KEY_ID), "
                "EGRESS_S3_KEY_SECRET (or S3_KEY_SECRET), "
                "S3_BUCKET, S3_REGION in the environment."
            )
        }

    if cfg["backend"] == "s3":
        missing = [k for k in ("egress_key_id", "egress_key_secret", "bucket", "region") if not cfg[k]]
        if missing:
            return 500, {"error": f"S3 not fully configured: {missing}"}
        file_output = EncodedFileOutput(
            filepath=f"recordings/{filename}",
            s3=S3Upload(
                access_key=cfg["egress_key_id"],
                secret=cfg["egress_key_secret"],
                bucket=cfg["bucket"],
                region=cfg["region"],
                **({"endpoint": cfg["endpoint"]} if cfg["endpoint"] else {}),
            ),
        )
    else:
        file_output = EncodedFileOutput(filepath=filepath)
    logger.info(
        "recording output prepared: "
        f"room={room_name} backend={cfg['backend']} filepath={file_output.filepath} "
        f"s3_bucket_set={bool(cfg['bucket'])} s3_region={cfg['region'] or ''} "
        f"s3_endpoint_set={bool(cfg['endpoint'])}"
    )

    try:
        async with api.LiveKitAPI(
            url=_lk_http_url(), api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET
        ) as lk:
            # Check for active egress
            existing = await lk.egress.list_egress(ListEgressRequest(room_name=room_name))
            active = [e for e in existing.items if e.status < 2]
            if active:
                return 409, {"error": "room already has an active recording egress"}

            egress_info = await _start_room_composite_egress(lk, room_name, file_output)
            egress_id = egress_info.egress_id
    except Exception as exc:
        logger.error(f"recording start LiveKit error: room={room_name} error={exc}")
        msg = str(exc)
        status = 404 if "not_found" in msg or "does not exist" in msg else 502
        return status, {"error": f"LiveKit: {msg}"}

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
    return 200, {"media_file_id": media_file_id, "egress_id": egress_id, "path": filepath}


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

    try:
        async with api.LiveKitAPI(
            url=_lk_http_url(), api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET
        ) as lk:
            existing = await lk.egress.list_egress(ListEgressRequest(room_name=room_name))
            active = [e for e in existing.items if e.status < 2]
            if not active:
                return JSONResponse({"error": "no active recording found"}, status_code=404)
            for e in active:
                await lk.egress.stop_egress(StopEgressRequest(egress_id=e.egress_id))
    except Exception as exc:
        logger.error(f"recording stop LiveKit error: room={room_name} error={exc}")
        return JSONResponse({"error": f"LiveKit: {exc}"}, status_code=502)

    logger.info(f"recording stopped: room={room_name} egress_count={len(active)}")
    return {"stopped": len(active)}


async def reconcile_stale_conversations(min_age_seconds: int = 60) -> int:
    """Close conversations stuck on 'running' whose LiveKit room no longer exists.

    The bot's own finally block normally writes the terminal status, but if the bot
    process dies hard (OOM under load, kill, crash) that block never runs and the row
    stays 'running' forever. This sweep is the safety net: it lists active LiveKit
    rooms and marks any running conversation whose room is gone as 'ended'.

    min_age_seconds guards against racing a freshly-created session whose bot has not
    yet joined — the room doesn't exist in LiveKit until the bot connects.
    """
    from livekit.protocol.room import ListRoomsRequest

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=min_age_seconds)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Conversation).where(
                Conversation.status == "running",
                Conversation.started_at < cutoff,
            )
        )
        running = list(result.scalars().all())
    if not running:
        return 0

    async with api.LiveKitAPI(
        url=_lk_http_url(), api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET
    ) as lk:
        rooms = (await lk.room.list_rooms(ListRoomsRequest())).rooms
    active_rooms = {r.name for r in rooms}

    stale = [c for c in running if c.room_name not in active_rooms]
    if not stale:
        return 0

    async with AsyncSessionLocal() as db, db.begin():
        closed = await sessions.end(db, [c.id for c in stale], "room_gone")
    if closed:
        logger.info(f"reconcile: closed {len(closed)} stale conversation(s): {', '.join(closed)}")
    return len(closed)


@app.on_event("startup")
async def _install_event_log_sink() -> None:
    """Mirror WARNING+ logs from the runner and every bot into the event log."""
    install_error_event_sink(asyncio.get_running_loop())
    logger.info("event-log sink installed (WARNING+ mirrored to events table)")


_RECONCILER_SESSION: list = []


@app.on_event("startup")
async def _start_conversation_reconcile_loop() -> "asyncio.Task | None":
    if os.environ.get("DISABLE_CONVERSATION_RECONCILE", "").lower() in ("1", "true", "yes"):
        return

    # Every process reconciles; none is elected. An election that runs once at startup
    # left nothing reconciling after every rolling deploy (the old task held the lock
    # while the new one started). Each write below is conditional on 'running', so N
    # processes cost N LiveKit round-trips per tick, never a wrong status.

    interval = int(os.environ.get("CONVERSATION_RECONCILE_INTERVAL_SECONDS", "120"))

    async def _loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await reconcile_stale_conversations()
                await fail_silent_sessions(AsyncSessionLocal, stop=_stop_bot)
            except Exception as e:
                logger.warning(f"conversation reconcile loop error: {e}")

    loop = asyncio.create_task(_loop())
    logger.info(f"conversation reconcile loop started (every {interval}s)")
    return loop


@app.post("/recordings/reconcile")
async def reconcile_recordings(
    background_tasks: BackgroundTasks, _=Depends(verify_api_key)
):
    """Query LiveKit egress status for all pending recordings and sync MediaFile state.

    Webhooks can be missed (network issues, redeployments, LiveKit Cloud timing).
    The meetings UI calls this whenever it detects recordings stuck in 'pending'.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(MediaFile).where(
                MediaFile.type == "recording",
                MediaFile.status == "pending",
            )
        )
        pending = list(result.scalars().all())

    if not pending:
        return {"checked": 0}

    background_tasks.add_task(_reconcile_pending_recordings, pending)
    logger.info(f"reconcile: queued check for {len(pending)} pending recording(s)")
    return {"checked": len(pending)}


async def _reconcile_pending_recordings(pending: list) -> None:
    """Background task: compare pending MediaFiles against LiveKit egress state."""
    from livekit.protocol.egress import ListEgressRequest

    # EgressStatus numeric values from the LiveKit protocol:
    #   0=STARTING, 1=ACTIVE, 2=ENDING, 3=COMPLETE, 4=FAILED, 5=ABORTED, 6=LIMIT_REACHED
    TERMINAL_AVAILABLE = (3,)
    TERMINAL_FAILED = (4, 5, 6)

    try:
        async with api.LiveKitAPI(
            url=_lk_http_url(), api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET
        ) as lk:
            for mf in pending:
                egress_id = (mf.meta or {}).get("egress_id")
                if not egress_id:
                    continue
                try:
                    resp = await lk.egress.list_egress(
                        ListEgressRequest(egress_id=egress_id)
                    )
                    if not resp.items:
                        logger.warning(
                            f"reconcile: egress {egress_id} not found on LiveKit "
                            f"(media_file={mf.id})"
                        )
                        continue

                    egress = resp.items[0]
                    status_code = egress.status
                    if status_code in TERMINAL_AVAILABLE:
                        new_status = "available"
                    elif status_code in TERMINAL_FAILED:
                        new_status = "failed"
                    else:
                        logger.debug(
                            f"reconcile: egress {egress_id} still in progress "
                            f"(status={status_code})"
                        )
                        continue

                    async with AsyncSessionLocal() as db:
                        async with db.begin():
                            await db.execute(
                                update(MediaFile)
                                .where(MediaFile.id == mf.id)
                                .values(status=new_status)
                            )
                    logger.info(
                        f"reconcile: media_file={mf.id} → {new_status} "
                        f"(egress={egress_id}, livekit_status={status_code})"
                    )
                except Exception as exc:
                    logger.warning(
                        f"reconcile: error checking egress {egress_id}: {exc}"
                    )
    except Exception as exc:
        logger.error(f"reconcile: LiveKit API connection error: {exc}")


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
    media_types = {".mp4": "video/mp4", ".md": "text/markdown", ".txt": "text/plain", ".wav": "audio/wav"}
    media_type = media_types.get(ext, "application/octet-stream")
    return FileResponse(path=str(file_path), media_type=media_type, filename=file_path.name)


def _zip_entries(entries: list[tuple[str, bytes]]) -> bytes:
    """Bundle (filename, bytes) pairs into an in-memory zip archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


@app.get("/conversations/{conv_id}/audio-tracks/download")
async def download_audio_tracks(conv_id: str, _=Depends(verify_api_key)):
    """Bundle all per-speaker audio tracks for a conversation into one zip.

    One download for the whole meeting's source-separated audio, instead of a
    file-per-speaker. Built in memory (WAVs are modest and this is an admin
    action); for very long multi-speaker sessions this holds the archive in RAM.
    """
    async with AsyncSessionLocal() as db:
        conv = (await db.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )).scalar_one_or_none()
        if not conv:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        rows = (await db.execute(
            select(MediaFile).where(
                MediaFile.conv_id == conv_id,
                MediaFile.type == "audio_track",
                MediaFile.status == "available",
            )
        )).scalars().all()

    entries: list[tuple[str, bytes]] = []
    for mf in rows:
        if not mf.path:
            continue
        try:
            entries.append((os.path.basename(mf.path), await storage.read_bytes(mf.path)))
        except Exception as exc:
            logger.warning(f"audio-tracks zip: skipping {mf.path}: {exc}")

    if not entries:
        return JSONResponse({"error": "no audio tracks for this conversation"}, status_code=404)

    safe_room = "".join(c if c.isalnum() or c in "-_" else "_" for c in conv.room_name)
    filename = f"{safe_room}-audio-tracks.zip"
    return Response(
        content=_zip_entries(entries),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    await _reconcile_pending_local_recordings()

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


@app.get("/conversations/{conv_id}/utterances")
async def conversation_utterances(conv_id: str, _=Depends(verify_api_key)):
    """A conversation's turns as data, in order: exact time, who spoke, what was said.
    Load tests score quality from it (quality.py); the Markdown transcript is for people."""
    async with AsyncSessionLocal() as db:
        if not await db.get(Conversation, conv_id):
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        rows = (await db.execute(
            select(Utterance).options(selectinload(Utterance.speaker))
            .where(Utterance.conv_id == conv_id).order_by(Utterance.ts)
        )).scalars().all()
    return {"utterances": [{"speaker": u.speaker_id, "bot": bool(u.speaker and u.speaker.meta.get("role") == "bot"),
                            "ts": u.ts, "text": u.text} for u in rows]}


@app.get("/conversations/{conv_id}/speakers")
async def conversation_speakers(conv_id: str, _=Depends(verify_api_key)):
    """Who took part, in the order they first spoke, with the Prolific ID a paid study is
    matched and paid on (docs/study-support.md). People only, not the bot."""
    async with AsyncSessionLocal() as db:
        if not await db.get(Conversation, conv_id):
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        rows = (await db.execute(
            select(Speaker, func.min(Utterance.ts)).join(Utterance, Utterance.speaker_id == Speaker.id)
            .where(Utterance.conv_id == conv_id).group_by(Speaker.id).order_by(func.min(Utterance.ts))
        )).all()
    return {"speakers": [{"speaker": sp.id, "display_name": sp.meta.get("display_name"),
                          "prolific_id": sp.meta.get("prolific_id"), "prolific_id_invalid": sp.meta.get("prolific_id_invalid")}
                         for sp, _ in rows if sp.meta.get("role") != "bot"]}


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

    from process_concurrency import worker_count

    workers = worker_count()
    logger.info(
        f"Starting LiveKit Bot Runner — LiveKit URL: {LIVEKIT_URL}, "
        f"worker processes: {workers}"
    )
    if workers == 1:
        # Pass the app object so local runs stay debuggable without an import
        # string.
        uvicorn.run(app, host="0.0.0.0", port=7860)
    else:
        # Several processes, each with its own interpreter, GIL and event loop.
        # Bots run as asyncio tasks inside whichever process served their /start,
        # so concurrency scales with process count. The 2026-08-20 ramp stalled
        # at ten sessions on a single process while CPU sat at 80% — one GIL
        # cannot service ten pipelines, and more cores do not change that.
        # uvicorn needs an import string in order to fork.
        uvicorn.run("runner:app", host="0.0.0.0", port=7860, workers=workers)
