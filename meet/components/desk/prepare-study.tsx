'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
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
    <section className="border-foreground/20 space-y-3 border p-4">
      <h2 className="text-lg font-medium">Prepare for study</h2>
      <p className="text-sm" aria-live="polite">
        {status ? describeCapacity(status) : 'Checking…'}
      </p>
      {status?.available !== false && (
        <form onSubmit={prepare} className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Sessions at once
            <input
              type="number"
              min={1}
              required
              value={sessions}
              onChange={(e) => setSessions(Number(e.target.value))}
              className="border-foreground/20 w-28 border bg-transparent px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Until
            <input
              type="datetime-local"
              required
              value={until}
              onChange={(e) => setUntil(e.target.value)}
              className="border-foreground/20 border bg-transparent px-3 py-2"
            />
          </label>
          <Button type="submit" disabled={busy}>
            Prepare
          </Button>
          <Button type="button" variant="outline" disabled={busy} onClick={() => void act(() => call('DELETE'))}>
            Stop preparing
          </Button>
        </form>
      )}
      {message && <p className="text-destructive text-sm">{message}</p>}
    </section>
  );
}
