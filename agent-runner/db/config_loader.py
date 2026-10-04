from dataclasses import dataclass

from sqlalchemy import select

from db.engine import AsyncSessionLocal
from db.models import BotConfig
from study_support import CLOSING_MESSAGE


@dataclass
class EffectiveBotConfig:
    system_prompt: str
    greeting: str
    llm_model: str
    tts_voice: str
    stt_model: str
    stt_vad_mode: str  # "server" | "local"
    stt_delay: str | None  # "minimal" | "low" | "medium" | "high" | "xhigh" | None
    tts_provider: str  # "elevenlabs" | "openai"
    tts_aggregation_mode: str  # "sentence" | "token"
    # The turn-end window is the SUM of these two: a turn commits after roughly
    # stt_endpointing_ms + user_speech_timeout_ms of silence. 450+300=750ms is
    # calibrated against real pilot audio — see tests/test_turn_calibration.py.
    stt_endpointing_ms: int
    auto_record: bool  # auto-start recording when the first participant joins
    # Advisory session cap in minutes, measured from the first human join; 0 = unlimited.
    # Defaulted so callers predating the field keep the old (uncapped) behaviour.
    session_limit_minutes: int = 0
    # Second half of the turn-end window (see stt_endpointing_ms above). Defaulted
    # so callers predating the field keep the calibrated behaviour.
    user_speech_timeout_ms: int = 300
    # Spoken when the session limit elapses. Only used when a limit is set.
    closing_message: str = CLOSING_MESSAGE
    turn_detection: str = "silence"  # "silence" | "smart_turn" (smart_turn.py)


async def load_bot_config(room_name: str | None = None) -> EffectiveBotConfig:
    """Return the effective bot config: room-specific row if it exists, else global."""
    async with AsyncSessionLocal() as db:
        row: BotConfig | None = None

        if room_name:
            result = await db.execute(
                select(BotConfig).where(BotConfig.scope == room_name)
            )
            row = result.scalar_one_or_none()

        if row is None:
            result = await db.execute(
                select(BotConfig).where(BotConfig.scope == "global")
            )
            row = result.scalar_one_or_none()

        if row is None:
            # Fallback hardcoded defaults if the table is somehow empty
            return EffectiveBotConfig(
                system_prompt=(
                    "You are a helpful assistant in a WebRTC call. "
                    "Your output will be converted to audio so don't include special characters. "
                    "Respond to what the user said in a creative and helpful way."
                ),
                greeting="Hello! How are you doing today?",
                llm_model="gpt-5.4-nano",
                tts_voice="WhMcMcvXQ8T2QfmQmlYh",
                stt_model="parakeet-tdt-0.6b-v2",
                stt_vad_mode="local",
                stt_delay=None,
                tts_provider="elevenlabs",
                tts_aggregation_mode="sentence",
                turn_detection="silence",
                # Must track the bot_config column defaults. This said 100 —
                # the pilot value RC2 fixed — so an empty table silently
                # reinstated the behaviour that cut participants off.
                stt_endpointing_ms=450,
                user_speech_timeout_ms=300,
                auto_record=False,
                session_limit_minutes=0,
                closing_message=CLOSING_MESSAGE,
            )

        return EffectiveBotConfig(
            system_prompt=row.system_prompt,
            greeting=row.greeting,
            llm_model=row.llm_model,
            tts_voice=row.tts_voice,
            stt_model=row.stt_model,
            stt_vad_mode=row.stt_vad_mode,
            stt_delay=getattr(row, "stt_delay", None),
            tts_provider=getattr(row, "tts_provider", "elevenlabs"),
            tts_aggregation_mode=getattr(row, "tts_aggregation_mode", "sentence"),
            turn_detection=getattr(row, "turn_detection", None) or "silence",
            stt_endpointing_ms=getattr(row, "stt_endpointing_ms", 450),
            user_speech_timeout_ms=int(getattr(row, "user_speech_timeout_ms", 300) or 300),
            auto_record=bool(getattr(row, "auto_record", False)),
            session_limit_minutes=int(getattr(row, "session_limit_minutes", 0) or 0),
            closing_message=getattr(row, "closing_message", None) or CLOSING_MESSAGE,
        )
