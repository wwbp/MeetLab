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
                llm_model="gpt-4.1",
                tts_voice="WhMcMcvXQ8T2QfmQmlYh",
            )

        return EffectiveBotConfig(
            system_prompt=row.system_prompt,
            greeting=row.greeting,
            vad_stop_secs=row.vad_stop_secs,
            llm_model=row.llm_model,
            tts_voice=row.tts_voice,
        )
