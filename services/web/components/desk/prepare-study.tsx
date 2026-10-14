'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { Label, buttonClass, inputClass, secondaryButtonClass } from '@/components/console/swiss';
import { CapacityStatus, describeCapacity, untilFromLocal } from '@/lib/prepare-study';

// Prepare for study: warm the bot pool before the first room opens, so no participant
// waits for a machine to boot. The pool cools down by itself at the end time.

function localIn(hours: number): string {
  const d = new Date(Date.now() + hours * 3600_000);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

async function call(method: string, body?: unknown): Promise<CapacityStatus & { capped?: boolean }> {
  const res = await fetch('/api/concierge/capacity', {
    method,
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
  return data;
}

export function PrepareStudy() {
  const [status, setStatus] = useState<CapacityStatus | null>(null);
  const [sessions, setSessions] = useState(6);
  const [until, setUntil] = useState(() => localIn(3));
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await call('GET'));
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(t);
  }, [refresh]);

  async function act(run: () => Promise<CapacityStatus & { capped?: boolean }>) {
    setBusy(true);
    setMessage(null);
    try {
      const s = await run();
      setStatus(s);
      if (s.capped) setMessage('This environment cannot hold that many sessions at once; it is prepared as far as it can be.');
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function prepare(e: FormEvent) {
    e.preventDefault();
    void act(() => call('POST', { sessions, until: untilFromLocal(until) }));
  }

  return (
    <div className="space-y-4">
      <p className="text-sm" aria-live="polite">
        {status ? describeCapacity(status) : 'Checking…'}
      </p>
      {status?.available !== false && (
        <form onSubmit={prepare} className="flex flex-wrap items-end gap-4">
          <label className="block w-36 space-y-1.5">
            <Label>Sessions at once</Label>
            <input type="number" min={1} required value={sessions} onChange={(e) => setSessions(Number(e.target.value))} className={inputClass} />
          </label>
          <label className="block space-y-1.5">
            <Label>Until</Label>
            <input type="datetime-local" required value={until} onChange={(e) => setUntil(e.target.value)} className={inputClass} />
          </label>
          <button type="submit" disabled={busy} className={buttonClass}>Prepare</button>
          <button type="button" disabled={busy} onClick={() => void act(() => call('DELETE'))} className={secondaryButtonClass}>Stop preparing</button>
        </form>
      )}
      {message && <p className="text-signal text-sm">{message}</p>}
    </div>
  );
}
