'use client';

import { createContext, useContext, useEffect, useState, type FormEvent } from 'react';
import { EMPTY_CONFIG, type BotConfig } from '@/lib/bot-config';
import { CONFIG_INFO, SECTIONS } from '@/lib/config-info';

// Which setting (or category) the info panel explains: the one hovered or focused last.
const Explain = createContext<(key: string) => void>(() => {});

const STT_MODELS = [
  { value: 'parakeet-tdt-0.6b-v2',   label: 'parakeet-tdt-0.6b-v2 (self-hosted GPU) (default)' },
  { value: 'parakeet-unified-en-0.6b', label: 'parakeet-unified-en-0.6b (self-hosted, offline)' },
  { value: 'nova-3-general',         label: 'nova-3-general (Deepgram)' },
  { value: 'gpt-realtime-whisper',   label: 'gpt-realtime-whisper (OpenAI)' },
  { value: 'gpt-4o-transcribe',      label: 'gpt-4o-transcribe (OpenAI)' },
  { value: 'gpt-4o-mini-transcribe', label: 'gpt-4o-mini-transcribe (OpenAI)' },
];

// Settings that only some speech-to-text models use (agent-runner/bot.py _build_stt).
const isOpenAiStt = (model: string) => model.startsWith('gpt-');
// Smart turn needs a per-speaker segmenting recogniser (smart_turn.py): Parakeet or Whisper.
const isSegmentedStt = (model: string) => model.startsWith('parakeet-') || model.startsWith('whisper-');

const LLM_MODELS = [
  'gpt-5.4-nano',
  'Qwen/Qwen2.5-7B-Instruct', // ours (vLLM), when staging runs it
  'gpt-5.4-mini',
  'gpt-4.1-nano',
  'gpt-4.1-mini',
  'gpt-4o-mini',
];

const TTS_PROVIDERS = ['elevenlabs', 'openai', 'kokoro']; // kokoro: our server, OpenAI voice names

export default function ConfigPage() {
  const [scope, setScope] = useState('global');
  const [form, setForm] = useState<Omit<BotConfig, 'scope'>>(EMPTY_CONFIG);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  async function loadConfig(s: string) {
    setLoading(true);
    setNotice('');
    setError('');
    try {
      const res = await fetch(`/api/console/config?scope=${encodeURIComponent(s)}`, {
        cache: 'no-store',
      });
      const data: BotConfig = await res.json();
      if (!res.ok) {
        setError((data as unknown as { error?: string }).error ?? 'Failed to load config');
        return;
      }
      const loadedLlm = data.llm_model ?? 'gpt-5.4-nano';
      const loadedStt = data.stt_model ?? 'parakeet-tdt-0.6b-v2';
      setForm({
        system_prompt: data.system_prompt ?? '',
        greeting: data.greeting ?? '',
        stt_model: STT_MODELS.some((m) => m.value === loadedStt) ? loadedStt : 'parakeet-tdt-0.6b-v2',
        llm_model: LLM_MODELS.includes(loadedLlm) ? loadedLlm : LLM_MODELS[0],
        tts_provider: data.tts_provider ?? 'elevenlabs',
        tts_voice: data.tts_voice ?? '',
        tts_aggregation_mode: data.tts_aggregation_mode ?? 'sentence',
        turn_detection: data.turn_detection ?? 'silence',
        smart_turn_wait_ms: data.smart_turn_wait_ms ?? 3000,
        stt_endpointing_ms: data.stt_endpointing_ms ?? 450,
        user_speech_timeout_ms: data.user_speech_timeout_ms ?? 50,
        stt_vad_mode: data.stt_vad_mode ?? 'local',
        stt_delay: data.stt_delay ?? null,
        auto_record: data.auto_record ?? false,
        session_limit_minutes: data.session_limit_minutes ?? 0,
        closing_message: data.closing_message ?? '',
      });
    } catch {
      setError('Network error loading config');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadConfig(scope);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setNotice('');
    setError('');
    try {
      const res = await fetch('/api/console/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope, ...form }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError((data as { error?: string }).error ?? 'Save failed');
        return;
      }
      setNotice(`Saved config for scope "${scope}"`);
    } catch {
      setError('Network error saving config');
    } finally {
      setSaving(false);
    }
  }

  function handleScopeChange(newScope: string) {
    setScope(newScope);
    loadConfig(newScope);
  }

  const [active, setActive] = useState<string>(SECTIONS[0].id);
  const explains = (key: string) => ({ onMouseEnter: () => setActive(key), onFocusCapture: () => setActive(key) });
  const sel =
    'border-input bg-background w-full rounded border px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring';
  const inp = sel;

  return (
    <Explain.Provider value={setActive}>
    <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-8 sm:py-10 lg:grid lg:grid-cols-[11rem_minmax(0,1fr)_22rem] lg:gap-10">
      {/* Categories: jump to one */}
      <nav className="hidden lg:block" aria-label="Categories">
        <div className="sticky top-6 space-y-1 pt-24">
          {SECTIONS.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              onClick={() => setActive(s.id)}
              className={`block rounded px-2 py-1.5 text-sm ${
                sectionOf(active) === s.id ? 'bg-muted font-medium' : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {s.label}
            </a>
          ))}
        </div>
      </nav>

      <div className="min-w-0 space-y-6">
      <header className="space-y-1">
        <p className="text-muted-foreground font-mono text-xs uppercase">Console</p>
        <h1 className="text-3xl font-medium">Bot Config</h1>
        <p className="text-muted-foreground text-sm">
          Scope <code>global</code> is the default; a room-specific row overrides it for that room.
        </p>
      </header>

      <div className="flex items-center gap-2">
        <label className="text-muted-foreground font-mono text-xs uppercase">Scope</label>
        <input
          type="text"
          value={scope}
          onChange={(e) => setScope(e.target.value)}
          onBlur={(e) => handleScopeChange(e.target.value.trim() || 'global')}
          className="border-input bg-background rounded border px-3 py-1.5 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          placeholder="global"
        />
        <span className="text-muted-foreground text-xs">global or a room name</span>
      </div>

      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-5">

          {/* ── Content ── */}
          <Section id="conversation" label="Conversation">
            <Field name="system_prompt" label="System Prompt" help="Who the bot is and how it should talk; sent to the language model with every turn.">
              <textarea
                value={form.system_prompt}
                onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))}
                rows={5}
                className={inp}
                required
              />
            </Field>
            <Field name="greeting" label="Greeting" help="What the bot says when the first person joins.">
              <textarea
                value={form.greeting}
                onChange={(e) => setForm((f) => ({ ...f, greeting: e.target.value }))}
                rows={2}
                className={inp}
                required
              />
            </Field>
          </Section>

          {/* ── STT: the model and the settings only it uses ── */}
          {/* ── Speech-to-Text and turn-taking: when someone has finished depends on the model ── */}
          <Section id="stt" label="Speech-to-Text and turn-taking">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field name="stt_model" label="STT Model" help="The speech-to-text model that turns what people say into text.">
                <select
                  value={form.stt_model}
                  onChange={(e) => setForm((f) => ({ ...f, stt_model: e.target.value }))}
                  className={sel}
                >
                  {STT_MODELS.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </Field>
              {isOpenAiStt(form.stt_model) && (
                <>
                  <Field name="stt_delay" label="Transcription delay (OpenAI only)" help="How long OpenAI waits for more words before finalising: longer is more accurate, slower.">
                    <select
                      value={form.stt_delay ?? ''}
                      onChange={(e) => setForm((f) => ({ ...f, stt_delay: e.target.value || null }))}
                      className={sel}
                    >
                      <option value="">default</option>
                      {['minimal', 'low', 'medium', 'high', 'xhigh'].map((d) => (
                        <option key={d} value={d}>{d}</option>
                      ))}
                    </select>
                  </Field>
                  <Field name="stt_vad_mode" label="Voice detection (OpenAI only)" help="Who decides when someone is speaking: always the bot’s own per-speaker detector.">
                    <select value={form.stt_vad_mode} className={sel} disabled>
                      <option value="local">local: the bot&apos;s own per-speaker detector</option>
                    </select>
                  </Field>
                </>
              )}
              <label className="flex items-start gap-3 sm:col-span-2" {...explains('turn_detection')}>
                <input
                  type="checkbox"
                  checked={form.turn_detection === 'smart_turn'}
                  disabled={!isSegmentedStt(form.stt_model)}
                  onChange={(e) => setForm((f) => ({ ...f, turn_detection: e.target.checked ? 'smart_turn' : 'silence' }))}
                  className="mt-0.5 h-4 w-4"
                />
                <span className="text-sm">
                  Smart turn{!isSegmentedStt(form.stt_model) && ' (Parakeet or Whisper only)'}
                  <span className="text-muted-foreground block text-xs">
                    Each speaker&apos;s own model hears when they sound finished, so a mid-sentence pause doesn&apos;t
                    end their turn (held 73% of such pauses on the pilot&apos;s real speech). Off: a turn ends after a silence.
                  </span>
                </span>
              </label>
              {form.turn_detection === 'smart_turn' && (
                <Field
                  name="smart_turn_wait_ms"
                  label="Wait for an unfinished speaker (ms)"
                  help="How long a turn that sounds unfinished stays open before the bot replies anyway: longer cuts fewer people off, shorter keeps fewer waiting."
                >
                  <input
                    type="number"
                    step="250"
                    min="500"
                    max="5000"
                    value={form.smart_turn_wait_ms}
                    onChange={(e) => setForm((f) => ({ ...f, smart_turn_wait_ms: parseInt(e.target.value, 10) }))}
                    className={inp}
                    required
                  />
                </Field>
              )}
              {/* The pause decides where a turn splits (450 ms, calibrated on pilot audio). The
                  wait comes after the transcript: with Parakeet or Whisper it only delays the
                  reply (B4, 2026-10-05: 300 → 50 ms was 0.24 s faster, splitting unchanged). */}
              <Field name="stt_endpointing_ms" label="Pause that counts as stopping (ms)" help="How long someone must be silent before their words count as finished: this decides where one turn ends and the next begins.">
                <input
                  type="number"
                  step="50"
                  min="50"
                  max="2000"
                  value={form.stt_endpointing_ms}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, stt_endpointing_ms: parseInt(e.target.value, 10) }))
                  }
                  className={inp}
                  required
                />
              </Field>
              <Field name="user_speech_timeout_ms" label="Extra wait before replying (ms)" help="After a person's words are transcribed, how long the bot waits before it answers. 50 ms is measured best with Parakeet; more only delays the reply.">
                <input
                  type="number"
                  step="50"
                  min="50"
                  max="2000"
                  value={form.user_speech_timeout_ms}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, user_speech_timeout_ms: parseInt(e.target.value, 10) }))
                  }
                  className={inp}
                  required
                />
              </Field>
            </div>
          </Section>

          {/* ── LLM ── */}
          <Section id="llm" label="Language Model">
            <Field name="llm_model" label="LLM Model" help="The language model that writes the bot’s replies.">
              <select
                value={form.llm_model}
                onChange={(e) => setForm((f) => ({ ...f, llm_model: e.target.value }))}
                className={sel}
              >
                {LLM_MODELS.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
          </Section>

          {/* ── TTS ── */}
          <Section id="voice" label="Voice (Text-to-Speech)">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field name="tts_provider" label="TTS Provider" help="The service that speaks the bot’s replies aloud.">
                <select
                  value={form.tts_provider}
                  onChange={(e) => setForm((f) => ({ ...f, tts_provider: e.target.value }))}
                  className={sel}
                >
                  {TTS_PROVIDERS.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </Field>
              <Field
                name="tts_voice"
                label={form.tts_provider === 'elevenlabs' ? 'Voice ID' : 'Voice Name'}
                help="Which voice the provider uses: an ElevenLabs voice ID, or a voice name (alloy) for OpenAI and Kokoro."
              >
                <input
                  type="text"
                  value={form.tts_voice}
                  onChange={(e) => setForm((f) => ({ ...f, tts_voice: e.target.value }))}
                  className={inp}
                  placeholder={form.tts_provider === 'elevenlabs' ? 'WhMcMcvXQ8T2QfmQmlYh' : 'alloy'}
                  required
                />
              </Field>
              <Field name="tts_aggregation_mode" label="Start speaking" help="How much of the reply the voice waits for before it speaks: a whole sentence, the first clause (sooner, then whole sentences), or each word as it arrives (ElevenLabs only: other voices make one request per word).">
                <select
                  value={form.tts_aggregation_mode}
                  onChange={(e) => setForm((f) => ({ ...f, tts_aggregation_mode: e.target.value }))}
                  className={sel}
                >
                  <option value="sentence">after the first full sentence (default)</option>
                  <option value="clause">after the first clause (sooner; then whole sentences)</option>
                  <option value="token">as words arrive (sooner, may sound choppier)</option>
                </select>
              </Field>
            </div>
          </Section>

          {/* ── Recording ── */}
          <Section id="recording" label="Recording">
            <label className="flex items-start gap-3" {...explains('auto_record')}>
              <input
                type="checkbox"
                checked={form.auto_record}
                onChange={(e) => setForm((f) => ({ ...f, auto_record: e.target.checked }))}
                className="mt-0.5 h-4 w-4"
              />
              <span className="text-sm">
                Auto-record sessions
                <span className="text-muted-foreground block text-xs">
                  Start recording (composite mp4 + per-speaker audio tracks) automatically
                  when the first participant joins. Ensure participants have consented.
                </span>
              </span>
            </label>
          </Section>

          {/* ── Session limit ── */}
          <Section id="session" label="Session Limit">
            <Field name="session_limit_minutes" label="Minutes (0 = unlimited)" help="Session length; the browser counts down and the bot says the closing message at the end.">
              <input
                type="number"
                step="1"
                min="0"
                max="1440"
                value={form.session_limit_minutes}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    session_limit_minutes: Math.max(
                      0,
                      Math.min(1440, Math.floor(Number(e.target.value) || 0)),
                    ),
                  }))
                }
                className={inp}
                required
              />
            </Field>
            <p className="text-muted-foreground text-xs">
              The countdown starts when the first person joins (not when the room is created or
              the bot starts) and is shared by everyone in the room. Participants see a timer, a
              warning three quarters of the way through, and a final reminder near the end. The
              limit is advisory — nobody is disconnected.
            </p>
            <Field name="closing_message" label="Closing message" help="What the bot says when time is up, e.g. where to paste the completion code.">
              <textarea
                value={form.closing_message}
                onChange={(e) => setForm((f) => ({ ...f, closing_message: e.target.value }))}
                rows={3}
                className={inp}
                required
              />
            </Field>
            <p className="text-muted-foreground text-xs">
              Spoken once when the limit runs out, waiting for a gap in the conversation so it is
              not interrupted away. Only ever said when a limit is set above. Mention the
              completion code — the participant sees it on screen after they leave, and pastes it
              into the study survey.
            </p>
          </Section>

          {error && <p className="text-destructive text-sm">{error}</p>}
          {notice && <p className="text-sm text-green-600 dark:text-green-400">{notice}</p>}

          <button
            type="submit"
            disabled={saving}
            className="bg-primary text-primary-foreground rounded px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save Config'}
          </button>
        </form>
      )}
      </div>

      {/* What the setting does, and how it performed in our tests */}
      <aside className="hidden lg:block" aria-label="About this setting">
        <div className="sticky top-6 pt-24">
          <InfoPanel active={active} />
        </div>
      </aside>
    </div>
    </Explain.Provider>
  );
}

const sectionOf = (key: string) => SECTIONS.find((s) => s.id === key || s.fields.includes(key))?.id;

function InfoPanel({ active }: { active: string }) {
  const section = SECTIONS.find((s) => s.id === active);
  if (section) {
    return (
      <div className="space-y-3 rounded border p-4">
        <p className="text-muted-foreground font-mono text-xs uppercase">{section.label}</p>
        <p className="text-sm">{section.about}</p>
        <p className="text-muted-foreground text-xs">Hover or select a setting to see what it does and how it performed in our tests.</p>
      </div>
    );
  }
  const info = CONFIG_INFO[active];
  if (!info) return null;
  return (
    <div className="space-y-3 rounded border p-4">
      <p className="text-muted-foreground font-mono text-xs uppercase">{SECTIONS.find((s) => s.fields.includes(active))?.label}</p>
      <h2 className="text-base font-medium">{info.title}</h2>
      <p className="text-sm leading-relaxed">{info.about}</p>
      {info.default && (
        <p className="text-sm">
          <span className="text-muted-foreground">Default: </span>
          {info.default}
        </p>
      )}
      {info.results && (
        <div className="space-y-2 border-t pt-3">
          <p className="text-muted-foreground font-mono text-xs uppercase">In our tests</p>
          {info.results.map((r) => (
            <div key={r.text} className="text-sm">
              <p className="leading-relaxed">{r.text}</p>
              <p className="text-muted-foreground mt-0.5 text-xs">{r.source}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Section({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  const explain = useContext(Explain);
  return (
    <div id={id} className="scroll-mt-6 space-y-3" onMouseEnter={() => explain(id)}>
      <p className="text-muted-foreground border-b pb-1 font-mono text-xs uppercase">{label}</p>
      {children}
    </div>
  );
}

function Field({ name, label, help, children }: { name: string; label: string; help?: string; children: React.ReactNode }) {
  const explain = useContext(Explain);
  return (
    <div className="space-y-1" onMouseEnter={() => explain(name)} onFocusCapture={() => explain(name)}>
      <label className="text-muted-foreground font-mono text-xs uppercase">{label}</label>
      {children}
      {help && <p className="text-muted-foreground text-xs">{help}</p>}
    </div>
  );
}
