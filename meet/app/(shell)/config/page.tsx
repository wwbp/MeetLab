'use client';

import { useEffect, useState, type FormEvent } from 'react';

type BotConfig = {
  scope: string;
  system_prompt: string;
  greeting: string;
  stt_model: string;
  llm_model: string;
  tts_provider: string;
  tts_voice: string;
  vad_stop_secs: number;
  stt_vad_mode: string;
  stt_delay: string | null;
};

const EMPTY_CONFIG: Omit<BotConfig, 'scope'> = {
  system_prompt: '',
  greeting: '',
  stt_model: 'parakeet-tdt-0.6b-v2',
  llm_model: 'gpt-5.4-nano',
  tts_provider: 'elevenlabs',
  tts_voice: 'WhMcMcvXQ8T2QfmQmlYh',
  vad_stop_secs: 0.6,
  stt_vad_mode: 'local',
  stt_delay: null,
};

const STT_MODELS = [
  { value: 'parakeet-tdt-0.6b-v2',   label: 'parakeet-tdt-0.6b-v2 (self-hosted GPU) (default)' },
  { value: 'parakeet-unified-en-0.6b', label: 'parakeet-unified-en-0.6b (self-hosted, offline)' },
  { value: 'nova-3-general',         label: 'nova-3-general (Deepgram)' },
  { value: 'gpt-realtime-whisper',   label: 'gpt-realtime-whisper (OpenAI)' },
  { value: 'gpt-4o-transcribe',      label: 'gpt-4o-transcribe (OpenAI)' },
  { value: 'gpt-4o-mini-transcribe', label: 'gpt-4o-mini-transcribe (OpenAI)' },
];

const LLM_MODELS = [
  'gpt-5.4-nano',
  'gpt-5.4-mini',
  'gpt-4.1-nano',
  'gpt-4.1-mini',
  'gpt-4o-mini',
];

const TTS_PROVIDERS = ['elevenlabs', 'openai'];

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
        vad_stop_secs: data.vad_stop_secs ?? 0.6,
        stt_vad_mode: data.stt_vad_mode ?? 'local',
        stt_delay: data.stt_delay ?? null,
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

  const sel =
    'border-input bg-background w-full rounded border px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring';
  const inp = sel;

  return (
    <div className="mx-auto w-full max-w-2xl space-y-6 px-4 py-6 sm:px-8 sm:py-10">
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
          <Section label="Content">
            <Field label="System Prompt">
              <textarea
                value={form.system_prompt}
                onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))}
                rows={5}
                className={inp}
                required
              />
            </Field>
            <Field label="Greeting">
              <textarea
                value={form.greeting}
                onChange={(e) => setForm((f) => ({ ...f, greeting: e.target.value }))}
                rows={2}
                className={inp}
                required
              />
            </Field>
          </Section>

          {/* ── STT ── */}
          <Section label="Speech-to-Text">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="STT Model">
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
              <Field label="VAD Stop (s)">
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  max="5"
                  value={form.vad_stop_secs}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, vad_stop_secs: parseFloat(e.target.value) }))
                  }
                  className={inp}
                  required
                />
              </Field>
            </div>
          </Section>

          {/* ── LLM ── */}
          <Section label="Language Model">
            <Field label="LLM Model">
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
          <Section label="Text-to-Speech">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="TTS Provider">
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
              <Field label={form.tts_provider === 'openai' ? 'Voice Name' : 'Voice ID'}>
                <input
                  type="text"
                  value={form.tts_voice}
                  onChange={(e) => setForm((f) => ({ ...f, tts_voice: e.target.value }))}
                  className={inp}
                  placeholder={form.tts_provider === 'openai' ? 'alloy' : 'WhMcMcvXQ8T2QfmQmlYh'}
                  required
                />
              </Field>
            </div>
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
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <p className="text-muted-foreground border-b pb-1 font-mono text-xs uppercase">{label}</p>
      {children}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-muted-foreground font-mono text-xs uppercase">{label}</label>
      {children}
    </div>
  );
}
