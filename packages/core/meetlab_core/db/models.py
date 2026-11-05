import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Integer,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# What the bot says when the session limit runs out. Overridable per room via
# bot_config.closing_message; this is the default the column ships with.
CLOSING_MESSAGE = (
    "That's all the time we have for today. Thank you so much for talking with me. "
    "Please leave the room now, and copy the completion code shown on your screen "
    "into the survey."
)



def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Speaker(Base):
    """One row per unique participant identity (human or bot).

    id is the LiveKit JWT identity string set at token creation time.
    """

    __tablename__ = "speakers"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    utterances: Mapped[list["Utterance"]] = relationship(back_populates="speaker")


class Conversation(Base):
    """One row per bot session (one bot join → leave arc in one room).

    root_utterance_id is set after the first utterance is saved; ConvoKit
    requires conversation_id == id of first utterance in the conversation.
    """

    __tablename__ = "conversations"
    # One running session per room, enforced by Postgres so it holds across runner
    # processes and instances; /start returns the running one instead of a second bot.
    __table_args__ = (
        Index("uq_conversations_one_running_per_room", "room_name",
              unique=True, postgresql_where=text("status = 'running'")),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    room_name: Mapped[str] = mapped_column(String(256), nullable=False)
    bot_identity: Mapped[str | None] = mapped_column(String(256))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(32), default="running"
    )  # running | completed | error | ended (reconciler-closed)
    root_utterance_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # Written by the running bot every heartbeat.BEAT; see heartbeat.py.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set by the runner when recording is requested; the bot (its own process since
    # 4c) reads it back on each heartbeat and starts per-speaker capture.
    recording_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    utterances: Mapped[list["Utterance"]] = relationship(back_populates="conversation")
    events: Mapped[list["Event"]] = relationship(back_populates="conversation")
    media_files: Mapped[list["MediaFile"]] = relationship(
        back_populates="conversation", order_by="MediaFile.created_at"
    )


class Utterance(Base):
    """One row per conversation turn (user speech or bot response).

    Maps directly to a ConvoKit utterance:
      id          → utterances.jsonl "id"
      speaker_id  → utterances.jsonl "speaker"
      conv_id     → utterances.jsonl "conversation_id"  (== conversations.root_utterance_id)
      reply_to    → utterances.jsonl "reply_to"
      ts          → utterances.jsonl "timestamp"  (unix float)
      text        → utterances.jsonl "text"
      meta        → utterances.jsonl "meta"  (latency_ms, confidence, interrupted, …)
    """

    __tablename__ = "utterances"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    speaker_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("speakers.id"), nullable=False
    )
    conv_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.id"), nullable=False
    )
    reply_to: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("utterances.id")
    )
    ts: Mapped[float | None] = mapped_column(Float)  # unix epoch
    text: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    speaker: Mapped["Speaker"] = relationship(back_populates="utterances")
    conversation: Mapped["Conversation"] = relationship(back_populates="utterances")


class MediaFile(Base):
    """One row per generated media file (recording, transcript, audio clip).

    type:   recording | transcript | audio_clip
    status: pending | available | failed
    path:   local filesystem path or S3 key — set at creation time
    meta:   egress_id (recordings), utterance_count (transcripts), size, etc.
    """

    __tablename__ = "media_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    conv_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    path: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    conversation: Mapped["Conversation"] = relationship(back_populates="media_files")


class Event(Base):
    """Append-only log of system events for research telemetry.

    Replaces the in-memory concierge event store (which was capped at 250).
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conv_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("conversations.id")
    )
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    # "info" | "warning" | "error" — a real column, not a payload key, so the
    # console can filter incidents out of a busy lifecycle log.
    severity: Mapped[str] = mapped_column(
        String(16), default="info", server_default="info", nullable=False, index=True
    )
    room_name: Mapped[str | None] = mapped_column(String(256), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )

    conversation: Mapped["Conversation | None"] = relationship(back_populates="events")


class BotConfig(Base):
    """Runtime configuration for the bot pipeline.

    scope='global' is the fallback; a row with scope=<room_name> overrides it.
    Editable via SQLAdmin at /admin.
    """

    __tablename__ = "bot_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(
        String(256), nullable=False, default="global", unique=True
    )
    system_prompt: Mapped[str] = mapped_column(
        Text,
        default=(
            "You are a helpful assistant in a WebRTC call. "
            "Your output will be converted to audio so don't include special characters. "
            "Respond to what the user said in a creative and helpful way."
        ),
    )
    greeting: Mapped[str] = mapped_column(
        Text, default="Hello! How are you doing today?"
    )
    llm_model: Mapped[str] = mapped_column(String(128), default="gpt-5.4-nano")
    tts_voice: Mapped[str] = mapped_column(String(64), default="WhMcMcvXQ8T2QfmQmlYh")
    stt_model: Mapped[str] = mapped_column(String(128), default="parakeet-tdt-0.6b-v2")
    stt_delay: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tts_provider: Mapped[str] = mapped_column(String(32), default="elevenlabs")
    tts_aggregation_mode: Mapped[str] = mapped_column(String(16), default="sentence")
    # How a person's turn is judged over (smart_turn.py): "silence" after a fixed pause, or
    # "smart_turn", each person's own Smart Turn v3 (segmented STT: Parakeet, Whisper).
    turn_detection: Mapped[str] = mapped_column(String(16), default="silence")
    # How long smart turn keeps an unfinished speaker's turn open before replying anyway.
    smart_turn_wait_ms: Mapped[int] = mapped_column(Integer, default=3000)
    stt_endpointing_ms: Mapped[int] = mapped_column(BigInteger, default=450, server_default="450")
    # Extra silence the turn aggregator waits after the VAD reports the speaker
    # stopped. ADDS to stt_endpointing_ms: a turn ends after roughly the sum of the
    # two. Calibrated against real pilot audio to 450+300=750ms — see
    # tests/integration/test_turn_calibration.py. Config-driven so the window can be tuned
    # against live conversations without a deploy.
    user_speech_timeout_ms: Mapped[int] = mapped_column(
        BigInteger, default=50, server_default="50", nullable=False  # B4, 2026-10-05
    )
    # When true, the bot auto-starts recording (composite mp4 + per-speaker WAV)
    # once the first participant joins. Off by default — opt in per room/global.
    auto_record: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Wall-clock cap on a session, in minutes, measured from the moment the first
    # human joins (not room creation, not bot start). 0 = unlimited. Advisory only:
    # the browser counts down and warns; nobody is disconnected.
    session_limit_minutes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    # Spoken once, when session_limit_minutes elapses, to tell the participant the
    # study is over and what to do next. Only reached when a limit is set.
    closing_message: Mapped[str] = mapped_column(
        Text,
        default=CLOSING_MESSAGE,
        server_default=CLOSING_MESSAGE,
        nullable=False,
    )
    # "normal" (open mic) | "ptt" | "baton" (docs/audio-modes-test-plan.md). Only stored and served
    # so far: nothing acts on it, so the console offers only normal.
    audio_mode: Mapped[str] = mapped_column(
        String, default="normal", server_default="normal", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


# --- Group start links (docs: group start links spec). A link's queue of tickets; the
# matchmaker (meetlab_api.matchmaker) turns enough live tickets into a match: one room.

TICKET_STATES = ("searching", "found", "ready", "placed", "done", "expired", "removed")
LIVE_TICKET_STATES = ("searching", "found", "ready", "placed")  # non-terminal


class StartLink(Base):
    """One start link. link_id is the URL's /start/<link_id>; a legacy (JWT) link's ID is
    the hash of its token, its row made on first use."""

    __tablename__ = "start_links"

    link_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    pool: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")  # bot_config scopes
    size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    min_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    delay_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_wait_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default="300")
    ready_check_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    # scrypt$<salt b64>$<hash b64> (links.hash_password); null = no password.
    password_hash: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Match(Base):
    """One formed group: a room, its bot setting and its members (tickets.room_name)."""

    __tablename__ = "matches"

    room_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    link_id: Mapped[str] = mapped_column(String(64), ForeignKey("start_links.link_id"), nullable=False, index=True)
    bot_config_scope: Mapped[str] = mapped_column(String(256), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)  # members
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ready_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # null when link size 1
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # null until all ready
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_id: Mapped[str | None] = mapped_column(String(64))  # set once setup_match started the bot
    setup_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Ticket(Base):
    """One participant's place in a link's queue.

    ponytail: the token is the raw UUID pk, visible to SQLAdmin users (console-only); store a
    hash if the Database page is ever opened wider.
    """

    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("state IN ('searching', 'found', 'ready', 'placed', 'done', 'expired', 'removed')",
                        name="ck_tickets_state"),
        # Same participant + same link → same ticket while it is non-terminal (R7).
        Index("uq_tickets_one_live_per_participant", "link_id", "participant_id", unique=True,
              postgresql_where=text("state IN ('searching', 'found', 'ready', 'placed')")),
        Index("ix_tickets_link_state", "link_id", "state"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    link_id: Mapped[str] = mapped_column(String(64), ForeignKey("start_links.link_id"), nullable=False)
    participant_id: Mapped[str | None] = mapped_column(String(256))  # null for size-1 one-off tickets
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="searching")
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requeued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    room_name: Mapped[str | None] = mapped_column(String(256), ForeignKey("matches.room_name"))
