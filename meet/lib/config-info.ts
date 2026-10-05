// What each Bot Config setting does, and what our tests measured (the config page's info panel).
// Numbers come from infra/v2/LEDGER.md and docs/load-test-report-2026-10.md; each names its source.

export type Result = { text: string; source: string };
export type Info = { title: string; about: string; default?: string; results?: Result[] };

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
    about:
      'Who the bot is and how it should talk. Sent to the language model with every turn, so it shapes every reply. Prompting is the researchers’: each study sets its own.',
    results: [
      {
        text: 'Adding one line ("Keep each reply to one or two short sentences, the way people speak in conversation.") cut replies from 46–89 words to 13–21 and raised the judged overall score from 3.4–4.0 to 4.5 out of 5.',
        source: VS_V1,
      },
    ],
  },
  greeting: {
    title: 'Greeting',
    about:
      'What the bot says about a second after the first person joins. Stored as the bot’s first turn. A bot that replaces one that died mid-meeting does not greet again; it carries on the conversation.',
  },
  stt_model: {
    title: 'Speech-to-text model',
    about:
      'Turns what each person says into text. Parakeet runs on our own GPU (the same model v1 uses); the others are paid services.',
    default: 'parakeet-tdt-0.6b-v2',
    results: [
      { text: 'Parakeet heard 2.3–2.9% of words wrong at every load from 6 to 102 rooms.', source: LOAD },
      { text: 'Pipecat’s benchmark: Parakeet 1.95% words wrong, final text 221 ms (typical) after speech ends; OpenAI gpt-4o-transcribe 637 ms typical, 1.66 s worst case.', source: 'Pipecat stt-benchmark, Sept 2026' },
    ],
  },
  stt_delay: {
    title: 'Transcription delay (OpenAI only)',
    about:
      'How long OpenAI’s speech-to-text waits for more words before it finalises: longer is more accurate, slower. Only used with an OpenAI model.',
  },
  stt_vad_mode: {
    title: 'Voice detection (OpenAI only)',
    about:
      'Who decides when someone is speaking. Always the bot’s own detector, one per speaker, so speakers stay apart.',
  },
  turn_detection: {
    title: 'Smart turn',
    about:
      'A small model per speaker listens to how they sound at each pause and judges whether they have finished. If they sound unfinished, their turn stays open for their next words instead of ending at the pause. Parakeet or Whisper only. Off: a turn ends after a pause.',
    default: 'off',
    results: [
      { text: 'On real participants’ speech it kept 66 of 91 mid-thought pauses open (73%) that the pause rule would have cut.', source: PILOT },
      { text: 'The cost: 38 of 93 real turn ends (41%) also sounded unfinished and waited for the backstop (the next setting).', source: PILOT },
      { text: 'Without it, about 24% of sentences are split into more than one turn.', source: B4 },
    ],
  },
  smart_turn_wait_ms: {
    title: 'Wait for an unfinished speaker',
    about:
      'With smart turn on: how long a turn that sounds unfinished stays open before the bot replies anyway. Longer cuts fewer people off; shorter keeps fewer people waiting. 3000 ms is Pipecat’s default.',
    default: '3000 ms',
  },
  stt_endpointing_ms: {
    title: 'Pause that counts as stopping',
    about:
      'How long someone must be silent before their words count as finished. This decides where one turn ends and the next begins.',
    default: '450 ms',
    results: [
      { text: 'Calibrated on real speech: 450 ms splits it into 1.34× as many pieces as the turns people really took (750 ms matched them). Splitting too much is the safe direction; too little makes the bot answer two things at once.', source: PILOT },
      { text: 'At 450 ms about 24% of sentences are split; smart turn reduces that.', source: B4 },
    ],
  },
  user_speech_timeout_ms: {
    title: 'Extra wait before replying',
    about:
      'After a person’s words are transcribed, how long the bot waits before it answers. It doesn’t change where turns split (that’s the pause above), so a longer wait only delays the reply.',
    default: '50 ms',
    results: [
      { text: '300 → 50 ms: replies 0.24 s faster (typical 1.45 → 1.21 s, slowest 1 in 20: 1.79 → 1.58 s); split sentences unchanged (24.5% → 24.6%); quality unchanged.', source: B4 },
    ],
  },
  llm_model: {
    title: 'Language model',
    about:
      'Writes the bot’s replies. Qwen 2.5 7B runs on our own GPU; the gpt models are OpenAI’s, paid per use.',
    results: [
      { text: 'Qwen starts answering after about 0.1 s; OpenAI’s gpt-5.4-nano after about 0.75 s. Judged answers on a par (4.0–4.2 vs 4.2–4.8 out of 5).', source: VS_V1 },
      { text: 'Qwen’s first word: 117 ms at light load, 198 ms at 102 rooms (slowest 1 in 20).', source: LOAD },
    ],
  },
  tts_provider: {
    title: 'Voice provider',
    about:
      'The service that speaks replies aloud. Kokoro runs on our own GPU (OpenAI voice names work with it); ElevenLabs and OpenAI are paid.',
    results: [
      { text: 'Kokoro: first sound 108 ms at light load, 220 ms at 102 rooms (two GPUs); listeners missed 1.8–4.8% of words; naturalness 4.3 out of 5 (predicted).', source: LOAD },
      { text: 'Naturalness (predicted): Kokoro 4.46, ElevenLabs 3.90. A human-rated sample should confirm it.', source: VS_V1 },
    ],
  },
  tts_voice: {
    title: 'Voice',
    about:
      'Which voice the provider uses: an ElevenLabs voice ID, or an OpenAI voice name such as alloy (also for Kokoro).',
  },
  tts_aggregation_mode: {
    title: 'Start speaking',
    about:
      'How much of the reply the voice waits for before it speaks. Sentence: a whole first sentence. Clause: the first clause (at least 4 words, ended by a comma, semicolon or colon), then whole sentences. Word: each word as it arrives, for ElevenLabs only (other voices would make one request per word).',
    default: 'sentence',
    results: [
      { text: 'Clause vs sentence: replies 35 ms sooner (first sentence 587 → 536 ms), quality unchanged. Small because replies are short (~10 words); it helps more with longer replies.', source: B4 },
      { text: 'Word mode with ElevenLabs was slower on replies longer than a sentence (+198 ms).', source: 'v1 latency experiments, June 2026' },
    ],
  },
  auto_record: {
    title: 'Auto-record',
    about:
      'Starts recording when the session starts: a video of the room and each speaker’s own audio. Make sure participants have consented. On our own LiveKit, video needs the recording server switched on before the study.',
    default: 'off',
  },
  session_limit_minutes: {
    title: 'Session limit',
    about:
      'Minutes per session, 0 for no limit. The countdown starts when the first person joins and is shared by everyone in the room; participants see a timer, a warning three quarters of the way through, and a final reminder. Advisory: nobody is disconnected.',
    default: '0 (no limit)',
    results: [{ text: 'Checked after every deploy: a 1-minute room hears its closing message after 61 s.', source: 'live check session_limit' }],
  },
  closing_message: {
    title: 'Closing message',
    about:
      'Spoken once when the limit runs out, at a gap in the conversation so it isn’t interrupted. Mention the completion code: participants see it on screen after they leave and paste it into the survey.',
  },
};
