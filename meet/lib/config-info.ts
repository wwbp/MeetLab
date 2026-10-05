// What each Bot Config setting does, and what our tests measured (the config page's info panel).
// Numbers come from infra/v2/LEDGER.md and docs/load-test-report-2026-10.md; each names its source.

export type Result = { text: string; source: string };
// key: the one result worth seeing at a glance; results: the detail behind "More".
export type Info = { title: string; about: string; default?: string; key?: { value: string; label: string }; results?: Result[] };

export const SECTIONS: { id: string; label: string; about: string; fields: string[] }[] = [
  {
    id: 'conversation',
    label: 'Conversation',
    about: 'What the bot is told to be, and the first thing it says.',
    fields: ['system_prompt', 'greeting'],
  },
  {
    id: 'stt',
    label: 'Speech-to-text and turn-taking',
    about:
      'How people’s speech becomes text, and when the bot decides someone has finished. Each person in the room is transcribed separately, so overlapping speakers keep their own words.',
    fields: ['stt_model', 'stt_delay', 'stt_vad_mode', 'turn_detection', 'smart_turn_wait_ms', 'stt_endpointing_ms', 'user_speech_timeout_ms'],
  },
  {
    id: 'llm',
    label: 'Language model',
    about: 'The model that writes the bot’s replies.',
    fields: ['llm_model'],
  },
  {
    id: 'voice',
    label: 'Voice',
    about: 'How the reply is spoken aloud.',
    fields: ['tts_provider', 'tts_voice', 'tts_aggregation_mode'],
  },
  {
    id: 'recording',
    label: 'Recording',
    about: 'Whether sessions are recorded without anyone pressing Record.',
    fields: ['auto_record'],
  },
  {
    id: 'session',
    label: 'Session limit',
    about: 'How long a session runs, and what the bot says when time is up.',
    fields: ['session_limit_minutes', 'closing_message'],
  },
];

const LOAD = 'load test, 2026-10-04 (6 → 102 rooms, our models)';
const B4 = 'latency test B4, 2026-10-05 (30 rooms, Parakeet)';
const VS_V1 = 'ours vs v1, 2026-10-03 (3 rooms, same recorded speech)';
const PILOT = 'July 2026 pilot audio (25 tracks, 52 min)';

export const CONFIG_INFO: Record<string, Info> = {
  system_prompt: {
    title: 'System prompt',
    about: 'Who the bot is and how it talks; sent with every turn.',
    key: { value: '4.5 / 5', label: 'judged quality with a short-replies line (from 3.4–4.0)' },
    results: [
      {
        text: 'Adding one line ("Keep each reply to one or two short sentences, the way people speak in conversation.") cut replies from 46–89 words to 13–21 and raised the judged overall score from 3.4–4.0 to 4.5 out of 5.',
        source: VS_V1,
      },
    ],
  },
  greeting: {
    title: 'Greeting',
    about: 'What the bot says when the first person joins.',
  },
  stt_model: {
    title: 'Speech-to-text model',
    about: 'Turns each person’s speech into text.',
    key: { value: '2.3–2.9%', label: 'words misheard by Parakeet, 6 to 102 rooms' },
    default: 'parakeet-tdt-0.6b-v2',
    results: [
      { text: 'Parakeet heard 2.3–2.9% of words wrong at every load from 6 to 102 rooms.', source: LOAD },
      { text: 'Pipecat’s benchmark: Parakeet 1.95% words wrong, final text 221 ms (typical) after speech ends; OpenAI gpt-4o-transcribe 637 ms typical, 1.66 s worst case.', source: 'Pipecat stt-benchmark, Sept 2026' },
    ],
  },
  stt_delay: {
    title: 'Transcription delay (OpenAI)',
    about: 'How long OpenAI waits for more words before finalising.',
  },
  stt_vad_mode: {
    title: 'Voice detection (OpenAI)',
    about: 'Who detects speech: always the bot, per speaker.',
  },
  turn_detection: {
    title: 'Smart turn',
    about: 'Waits for people who pause mid-thought, instead of cutting them off.',
    key: { value: '73%', label: 'of mid-thought pauses held open on real speech' },
    default: 'off',
    results: [
      { text: 'On real participants’ speech it kept 66 of 91 mid-thought pauses open (73%) that the pause rule would have cut.', source: PILOT },
      { text: 'The cost: 38 of 93 real turn ends (41%) also sounded unfinished and waited for the backstop (the next setting).', source: PILOT },
      { text: 'Without it, about 24% of sentences are split into more than one turn.', source: B4 },
    ],
  },
  smart_turn_wait_ms: {
    title: 'Wait for an unfinished speaker',
    about: 'How long an unfinished-sounding turn stays open.',
    default: '3000 ms',
  },
  stt_endpointing_ms: {
    title: 'Pause that ends a turn',
    about: 'The silence that ends a turn: decides where turns split.',
    key: { value: '1.34×', label: 'pieces vs real turns at 450 ms (safe direction)' },
    default: '450 ms',
    results: [
      { text: 'Calibrated on real speech: 450 ms splits it into 1.34× as many pieces as the turns people really took (750 ms matched them). Splitting too much is the safe direction; too little makes the bot answer two things at once.', source: PILOT },
      { text: 'At 450 ms about 24% of sentences are split; smart turn reduces that.', source: B4 },
    ],
  },
  user_speech_timeout_ms: {
    title: 'Wait before replying',
    about: 'Wait after the transcript before replying; only delays the reply.',
    key: { value: '−0.24 s', label: 'reply time at 50 ms vs 300 ms, quality unchanged' },
    default: '50 ms',
    results: [
      { text: '300 → 50 ms: replies 0.24 s faster (typical 1.45 → 1.21 s, slowest 1 in 20: 1.79 → 1.58 s); split sentences unchanged (24.5% → 24.6%); quality unchanged.', source: B4 },
    ],
  },
  llm_model: {
    title: 'Model',
    about: 'Writes the bot’s replies.',
    key: { value: '0.1 s', label: 'Qwen starts answering (OpenAI: 0.75 s)' },
    results: [
      { text: 'Qwen starts answering after about 0.1 s; OpenAI’s gpt-5.4-nano after about 0.75 s. Judged answers on a par (4.0–4.2 vs 4.2–4.8 out of 5).', source: VS_V1 },
      { text: 'Qwen’s first word: 117 ms at light load, 198 ms at 102 rooms (slowest 1 in 20).', source: LOAD },
    ],
  },
  tts_provider: {
    title: 'Voice provider',
    about: 'Speaks the replies aloud.',
    key: { value: '4.3 / 5', label: 'Kokoro naturalness; first sound 108–220 ms' },
    results: [
      { text: 'Kokoro: first sound 108 ms at light load, 220 ms at 102 rooms (two GPUs); listeners missed 1.8–4.8% of words; naturalness 4.3 out of 5 (predicted).', source: LOAD },
      { text: 'Naturalness (predicted): Kokoro 4.46, ElevenLabs 3.90. A human-rated sample should confirm it.', source: VS_V1 },
    ],
  },
  tts_voice: {
    title: 'Voice',
    about: 'Which voice the provider uses.',
  },
  tts_aggregation_mode: {
    title: 'Start speaking',
    about: 'How much of the reply the voice waits for before speaking.',
    key: { value: '−35 ms', label: 'first clause vs whole sentence (short replies)' },
    default: 'sentence',
    results: [
      { text: 'Clause vs sentence: replies 35 ms sooner (first sentence 587 → 536 ms), quality unchanged. Small because replies are short (~10 words); it helps more with longer replies.', source: B4 },
      { text: 'Word mode with ElevenLabs was slower on replies longer than a sentence (+198 ms).', source: 'v1 latency experiments, June 2026' },
    ],
  },
  auto_record: {
    title: 'Record every session',
    about: 'Records video and each speaker’s audio from the start.',
    default: 'off',
  },
  session_limit_minutes: {
    title: 'Length',
    about: 'Session length; a shared countdown from the first join.',
    default: '0 (no limit)',
    results: [{ text: 'Checked after every deploy: a 1-minute room hears its closing message after 61 s.', source: 'live check session_limit' }],
  },
  closing_message: {
    title: 'Closing message',
    about: 'Said once when time is up: mention the completion code.',
  },
};
