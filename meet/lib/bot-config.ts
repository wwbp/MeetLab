// The Bot Config every room runs with: the fields the console's form edits (app/(shell)/config).

export type BotConfig = {
  scope: string;
  system_prompt: string;
  greeting: string;
  stt_model: string;
  llm_model: string;
  tts_provider: string;
  tts_voice: string;
  tts_aggregation_mode: string;
  turn_detection: string;
  smart_turn_wait_ms: number;
  stt_endpointing_ms: number;
  user_speech_timeout_ms: number;
  stt_vad_mode: string;
  stt_delay: string | null;
  auto_record: boolean;
  session_limit_minutes: number;
  closing_message: string;
};

export const EMPTY_CONFIG: Omit<BotConfig, 'scope'> = {
  system_prompt: '',
  greeting: '',
  stt_model: 'parakeet-tdt-0.6b-v2',
  llm_model: 'gpt-5.4-nano',
  tts_provider: 'elevenlabs',
  tts_voice: 'WhMcMcvXQ8T2QfmQmlYh',
  tts_aggregation_mode: 'sentence',
  turn_detection: 'silence',
  smart_turn_wait_ms: 3000,
  stt_endpointing_ms: 450,
  user_speech_timeout_ms: 50,
  stt_vad_mode: 'local',
  stt_delay: null,
  auto_record: false,
  session_limit_minutes: 0,
  closing_message: '',
};
