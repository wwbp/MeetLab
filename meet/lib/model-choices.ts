// The models Bot settings offers, named for researchers: a plain name, who runs it, and a few
// words on when to pick it. `value` is what is stored (and what agent-runner/bot.py reads).

export type Choice = { value: string; name: string; runs: string; note?: string; isDefault?: boolean };

export const label = (c: Choice) => `${c.name} — ${c.runs}${c.isDefault ? ' (default)' : ''}`;

export const STT_MODELS: Choice[] = [
  { value: 'parakeet-tdt-0.6b-v2', name: 'Parakeet', runs: 'our own server', note: 'audio stays with us; measured 2–3% words misheard', isDefault: true },
  { value: 'nova-3-general', name: 'Deepgram Nova 3', runs: 'paid service: Deepgram', note: 'transcribes while people speak' },
  { value: 'gpt-4o-mini-transcribe', name: 'OpenAI Transcribe (mini)', runs: 'paid service: OpenAI' },
  { value: 'gpt-4o-transcribe', name: 'OpenAI Transcribe', runs: 'paid service: OpenAI', note: 'more accurate, slower' },
  { value: 'gpt-realtime-whisper', name: 'OpenAI Realtime Whisper', runs: 'paid service: OpenAI' },
];

export const LLM_MODELS: Choice[] = [
  { value: 'gpt-5.4-nano', name: 'GPT nano', runs: 'paid service: OpenAI', note: 'fastest replies', isDefault: true },
  { value: 'gpt-5.4-mini', name: 'GPT mini', runs: 'paid service: OpenAI', note: 'smarter, a little slower' },
  { value: 'Qwen/Qwen2.5-7B-Instruct', name: 'Qwen', runs: 'our own server (staging only)', note: 'conversation stays with us' },
  { value: 'gpt-4.1-nano', name: 'GPT nano, older', runs: 'paid service: OpenAI' },
  { value: 'gpt-4.1-mini', name: 'GPT mini, older', runs: 'paid service: OpenAI' },
  { value: 'gpt-4o-mini', name: 'GPT-4o mini, older', runs: 'paid service: OpenAI' },
];

export const TTS_PROVIDERS: Choice[] = [
  { value: 'elevenlabs', name: 'ElevenLabs', runs: 'paid service: ElevenLabs', note: 'most natural voices', isDefault: true },
  { value: 'openai', name: 'OpenAI voices', runs: 'paid service: OpenAI' },
  { value: 'kokoro', name: 'Kokoro', runs: 'our own server (staging only)', note: 'OpenAI voice names, e.g. alloy' },
];
