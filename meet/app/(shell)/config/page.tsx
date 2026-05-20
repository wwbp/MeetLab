'use client';

import { useEffect, useState, type FormEvent } from 'react';

type BotConfig = {
  scope: string;
  system_prompt: string;
  greeting: string;
  vad_stop_secs: number;
  llm_model: string;
  tts_voice: string;
};

const EMPTY_CONFIG: Omit<BotConfig, 'scope'> = {
  system_prompt: '',
  greeting: '',
  vad_stop_secs: 0.6,
  llm_model: 'gpt-4.1',
  tts_voice: 'WhMcMcvXQ8T2QfmQmlYh',
};

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
      setForm({
        system_prompt: data.system_prompt,
        greeting: data.greeting,
        vad_stop_secs: data.vad_stop_secs,
        llm_model: data.llm_model,
        tts_voice: data.tts_voice,
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

  return (
    <div className="mx-auto w-full max-w-2xl space-y-6 px-4 py-6 sm:px-8 sm:py-10">
      <header className="space-y-1">
        <p className="text-muted-foreground font-mono text-xs uppercase">Console</p>
        <h1 className="text-3xl font-medium">Bot Config</h1>
        <p className="text-muted-foreground text-sm">
          Edit the system prompt and voice settings. Scope <code>global</code> is the default;
          a room-specific row overrides it for that room.
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
        <span className="text-muted-foreground text-xs">
          Use <code>global</code> or a room name
        </span>
      </div>

      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="System Prompt">
            <textarea
              value={form.system_prompt}
              onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))}
              rows={6}
              className="border-input bg-background w-full rounded border px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              required
            />
          </Field>

          <Field label="Greeting">
            <textarea
              value={form.greeting}
              onChange={(e) => setForm((f) => ({ ...f, greeting: e.target.value }))}
              rows={2}
              className="border-input bg-background w-full rounded border px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              required
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="LLM Model">
              <input
                type="text"
                value={form.llm_model}
                onChange={(e) => setForm((f) => ({ ...f, llm_model: e.target.value }))}
                className="border-input bg-background w-full rounded border px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                required
              />
            </Field>

            <Field label="TTS Voice">
              <input
                type="text"
                value={form.tts_voice}
                onChange={(e) => setForm((f) => ({ ...f, tts_voice: e.target.value }))}
                className="border-input bg-background w-full rounded border px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                required
              />
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
                className="border-input bg-background w-full rounded border px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                required
              />
            </Field>
          </div>

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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-muted-foreground font-mono text-xs uppercase">{label}</label>
      {children}
    </div>
  );
}
