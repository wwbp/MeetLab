'use client';

import { useCallback, useEffect, useState } from 'react';
import type { ConciergeEvent, EventSeverity } from '@/lib/concierge/types';
import { cn } from '@/lib/utils';

const SEVERITY_FILTERS: { label: string; value: EventSeverity | 'all' }[] = [
  { label: 'Errors', value: 'error' },
  { label: 'Warnings', value: 'warning' },
  { label: 'Info', value: 'info' },
  { label: 'Everything', value: 'all' },
];

const SEVERITY_STYLE: Record<EventSeverity, string> = {
  error: 'bg-destructive/15 text-destructive',
  warning: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  info: 'bg-muted text-muted-foreground',
};

const SOURCE_LABEL: Record<ConciergeEvent['source'], string> = {
  concierge: 'meet',
  webhook: 'livekit',
  runner: 'runner',
  bot: 'bot',
};

const REFRESH_MS = 15_000;

/**
 * The pipeline's event log: admin actions from meet, LiveKit webhooks, and every
 * warning or error the runner and the bot logged — one timeline, newest first.
 *
 * Defaults to errors only. During a study session the interesting question is
 * "did anything break?", and the lifecycle stream is loud enough to hide it.
 */
export function EventsTab() {
  const [severity, setSeverity] = useState<EventSeverity | 'all'>('error');
  const [room, setRoom] = useState('');
  const [events, setEvents] = useState<ConciergeEvent[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (severity !== 'all') params.set('severity', severity);
    if (room.trim()) params.set('room', room.trim());
    try {
      const res = await fetch(`/api/concierge/events?${params}`, { cache: 'no-store' });
      const data = (await res.json()) as { events?: ConciergeEvent[]; error?: string };
      if (!res.ok) {
        // A read failure is not "no events" — say so, or the console lies.
        setError(data.error ?? `Event log unavailable (${res.status})`);
        return;
      }
      setEvents(data.events ?? []);
      setError('');
    } catch {
      setError('Network error reading the event log');
    } finally {
      setLoading(false);
    }
  }, [severity, room]);

  useEffect(() => {
    setLoading(true);
    load();
    const id = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 px-4 py-6 sm:px-8 sm:py-10">
      <header className="space-y-1">
        <p className="text-muted-foreground font-mono text-xs uppercase">Console</p>
        <h1 className="text-3xl font-medium">Errors &amp; Events</h1>
        <p className="text-muted-foreground text-sm">
          Everything the pipeline recorded — meet, LiveKit, the bot runner and the bots — newest
          first. Survives restarts and deploys.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        {SEVERITY_FILTERS.map(({ label, value }) => (
          <button
            key={value}
            onClick={() => setSeverity(value)}
            className={cn(
              'rounded px-3 py-1.5 text-sm transition-colors',
              severity === value
                ? 'bg-foreground text-background'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {label}
          </button>
        ))}
        <input
          value={room}
          onChange={(e) => setRoom(e.target.value)}
          placeholder="filter by room"
          className="border-input bg-background focus:ring-ring ml-auto rounded border px-3 py-1.5 font-mono text-sm focus:ring-1 focus:outline-none"
        />
        <button
          onClick={load}
          className="text-muted-foreground hover:text-foreground rounded border px-3 py-1.5 text-sm"
        >
          Refresh
        </button>
      </div>

      {error && (
        <p className="border-destructive/40 bg-destructive/10 text-destructive rounded border p-3 text-sm">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : events.length === 0 && !error ? (
        <p className="text-muted-foreground text-sm">
          No {severity === 'all' ? 'events' : `${severity}s`} recorded
          {room.trim() ? ` for room “${room.trim()}”` : ''}.
        </p>
      ) : (
        <div className="divide-border divide-y rounded border">
          {events.map((event) => {
            const isOpen = expanded === event.id;
            const payload = (event.payload ?? {}) as Record<string, unknown>;
            const hasPayload = Object.keys(payload).length > 0;
            return (
              <div key={event.id} className="p-3 text-sm">
                <button
                  onClick={() => setExpanded(isOpen ? null : event.id)}
                  className="flex w-full items-start gap-3 text-left"
                  aria-expanded={isOpen}
                >
                  <span
                    className={cn(
                      'rounded px-1.5 py-0.5 font-mono text-[0.65rem] uppercase',
                      SEVERITY_STYLE[event.severity],
                    )}
                  >
                    {event.severity}
                  </span>
                  <span className="text-muted-foreground font-mono text-xs whitespace-nowrap">
                    {new Date(event.receivedAt).toLocaleTimeString()}
                  </span>
                  <span className="text-muted-foreground font-mono text-xs">
                    {SOURCE_LABEL[event.source]}
                  </span>
                  <span className="min-w-0 flex-1 font-mono break-all">{event.event}</span>
                  {event.roomName && (
                    <span className="text-muted-foreground max-w-[14rem] truncate font-mono text-xs">
                      {event.roomName}
                    </span>
                  )}
                  {hasPayload && (
                    <span className="text-muted-foreground text-xs">{isOpen ? '−' : '+'}</span>
                  )}
                </button>
                {isOpen && hasPayload && (
                  <pre className="bg-muted text-muted-foreground mt-2 overflow-x-auto rounded p-2 text-xs">
                    {JSON.stringify(payload, null, 2)}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
