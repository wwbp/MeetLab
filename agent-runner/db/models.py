import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    room_name: Mapped[str] = mapped_column(String(256), nullable=False)
    bot_identity: Mapped[str | None] = mapped_column(String(256))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(32), default="running"
    )  # running | completed | error
    root_utterance_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

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
    room_name: Mapped[str | None] = mapped_column(String(256))
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
    vad_stop_secs: Mapped[float] = mapped_column(Float, default=0.6)
    llm_model: Mapped[str] = mapped_column(String(128), default="gpt-5.4-nano")
    tts_voice: Mapped[str] = mapped_column(String(64), default="WhMcMcvXQ8T2QfmQmlYh")
    stt_model: Mapped[str] = mapped_column(String(128), default="nova-3-general")
    stt_vad_mode: Mapped[str] = mapped_column(String(32), default="local")
    stt_delay: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tts_provider: Mapped[str] = mapped_column(String(32), default="elevenlabs")
    tts_aggregation_mode: Mapped[str] = mapped_column(String(16), default="sentence")
    stt_endpointing_ms: Mapped[int] = mapped_column(BigInteger, default=200, server_default="200")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
