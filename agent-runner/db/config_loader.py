from dataclasses import dataclass

from sqlalchemy import select

from db.engine import AsyncSessionLocal
from db.models import BotConfig


@dataclass
class EffectiveBotConfig:
    system_prompt: str
    greeting: str
    vad_stop_secs: float
    llm_model: str
    tts_voice: str
    stt_model: str
    stt_vad_mode: str  # "server" | "local"
    stt_delay: str | None  # "minimal" | "low" | "medium" | "high" | "xhigh" | None
    tts_provider: str  # "elevenlabs" | "openai"
    tts_aggregation_mode: str  # "sentence" | "token"
    stt_endpointing_ms: int  # 200 | 100 | 50


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
                vad_stop_secs=0.6,
                llm_model="gpt-5.4-nano",
                tts_voice="WhMcMcvXQ8T2QfmQmlYh",
                stt_model="nova-3-general",
                stt_vad_mode="local",
                stt_delay=None,
                tts_provider="elevenlabs",
                tts_aggregation_mode="sentence",
                stt_endpointing_ms=200,
            )

        return EffectiveBotConfig(
            system_prompt=row.system_prompt,
            greeting=row.greeting,
            vad_stop_secs=row.vad_stop_secs,
            llm_model=row.llm_model,
            tts_voice=row.tts_voice,
            stt_model=row.stt_model,
            stt_vad_mode=row.stt_vad_mode,
            stt_delay=getattr(row, "stt_delay", None),
            tts_provider=getattr(row, "tts_provider", "elevenlabs"),
            tts_aggregation_mode=getattr(row, "tts_aggregation_mode", "sentence"),
            stt_endpointing_ms=getattr(row, "stt_endpointing_ms", 200),
        )
