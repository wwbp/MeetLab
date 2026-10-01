import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from study_support import CLOSING_MESSAGE


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
    stt_vad_mode: Mapped[str] = mapped_column(String(32), default="local")
    stt_delay: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tts_provider: Mapped[str] = mapped_column(String(32), default="elevenlabs")
    tts_aggregation_mode: Mapped[str] = mapped_column(String(16), default="sentence")
    stt_endpointing_ms: Mapped[int] = mapped_column(BigInteger, default=450, server_default="450")
    # Extra silence the turn aggregator waits after the VAD reports the speaker
    # stopped. ADDS to stt_endpointing_ms: a turn ends after roughly the sum of the
    # two. Calibrated against real pilot audio to 450+300=750ms — see
    # tests/test_turn_calibration.py. Config-driven so the window can be tuned
    # against live conversations without a deploy.
    user_speech_timeout_ms: Mapped[int] = mapped_column(
        BigInteger, default=300, server_default="300", nullable=False
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
