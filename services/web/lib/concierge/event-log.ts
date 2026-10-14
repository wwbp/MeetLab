/**
 * Durable event log — meet's side.
 *
 * Everything meet knows about a session (admin actions, LiveKit webhooks, and
 * route failures) is written to agent-runner's `events` table, which is also
 * where the runner and the bot mirror their own warnings and errors. One table,
 * one timeline, joinable by room — that's what makes the console able to answer
 * "what happened in this session?" after a restart.
 *
 * Writes are best-effort and never throw: an event describes an operation and
 * must not be the thing that breaks it. Reads throw, because a console quietly
 * showing an empty list would read as "nothing went wrong".
 */
import { after } from 'next/server';
import { getServerConfig } from '@/lib/config/server';
import type { ConciergeEvent, EventSeverity } from '@/lib/concierge/types';

/** A row as agent-runner's `GET /events` returns it. */
type RunnerEventRow = {
  id: number | string;
  type: string;
  severity?: string;
  room_name?: string | null;
  conv_id?: string | null;
  payload?: Record<string, unknown> | null;
  created_at: string;
};

type ConciergeEventInput = Omit<ConciergeEvent, 'id' | 'receivedAt' | 'severity'> & {
  receivedAt?: string;
};

/** Keep a stack useful for debugging without letting it bloat the table. */
const MAX_STACK_CHARS = 2_048;

const KNOWN_SOURCES = new Set<ConciergeEvent['source']>(['concierge', 'webhook', 'runner', 'bot']);

function runnerBase(): string | null {
  const { botRunnerUrl } = getServerConfig();
  if (!botRunnerUrl) return null;
  return botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`;
}

function runnerHeaders(): Record<string, string> {
  const { botRunnerSecret } = getServerConfig();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (botRunnerSecret) headers.Authorization = `Bearer ${botRunnerSecret}`;
  return headers;
}

/** meet's event shape → the runner's `POST /events` body. */
export function toRunnerEvent(event: ConciergeEventInput, severity: EventSeverity = 'info') {
  const payload: Record<string, unknown> = { source: event.source };
  if (event.participantIdentity) payload.participant_identity = event.participantIdentity;
  if (event.payload !== undefined && event.payload !== null) {
    if (typeof event.payload === 'object' && !Array.isArray(event.payload)) {
      Object.assign(payload, event.payload as Record<string, unknown>);
    } else {
      payload.data = event.payload;
    }
  }
  return {
    type: event.event,
    severity,
    room_name: event.roomName ?? undefined,
    payload,
  };
}

/**
 * A stored row → the shape the console renders.
 *
 * `source` is recovered from the payload when meet wrote the row, and otherwise
 * inferred from the event type: the runner and the bot mirror their logs as
 * `bot.log.error` / `runner.log.warning` with no source key, and LiveKit's own
 * webhook types are bare words like `participant_joined`.
 */
export function fromRunnerRow(row: RunnerEventRow): ConciergeEvent {
  const payload = { ...(row.payload ?? {}) } as Record<string, unknown>;
  const rawSource = payload.source;
  delete payload.source;
  const participantIdentity = payload.participant_identity;
  delete payload.participant_identity;

  const prefix = row.type.split('.')[0] as ConciergeEvent['source'];
  const source =
    typeof rawSource === 'string' && KNOWN_SOURCES.has(rawSource as ConciergeEvent['source'])
      ? (rawSource as ConciergeEvent['source'])
      : !row.type.includes('.')
        ? // LiveKit's own webhook types are bare snake_case words.
          'webhook'
        : KNOWN_SOURCES.has(prefix)
          ? prefix
          : // Dotted but unrecognised: something wrote straight to the runner's
            // API without naming itself. Attributing it to LiveKit would be wrong.
            'runner';

  return {
    id: String(row.id),
    source,
    event: row.type,
    severity: (row.severity as EventSeverity) ?? 'info',
    receivedAt: row.created_at,
    roomName: row.room_name ?? undefined,
    participantIdentity: typeof participantIdentity === 'string' ? participantIdentity : undefined,
    payload,
  };
}

/** Write one event. Never throws. */
export async function recordEvent(
  event: ConciergeEventInput,
  severity: EventSeverity = 'info',
): Promise<void> {
  const base = runnerBase();
  if (!base) return;
  try {
    await fetch(`${base}events`, {
      method: 'POST',
      headers: runnerHeaders(),
      body: JSON.stringify(toRunnerEvent(event, severity)),
      cache: 'no-store',
    });
  } catch {
    // Telemetry is not worth an exception on the calling path.
  }
}

/** Record a route failure so it shows up in the console. Never throws. */
export async function reportRouteError(
  route: string,
  error: unknown,
  context: { roomName?: string; participantIdentity?: string } = {},
): Promise<void> {
  const message = error instanceof Error ? error.message : String(error);
  const stack = error instanceof Error ? error.stack?.slice(0, MAX_STACK_CHARS) : undefined;
  await recordEvent(
    {
      source: 'concierge',
      event: 'meet.route.error',
      roomName: context.roomName,
      participantIdentity: context.participantIdentity,
      payload: { route, message, ...(stack ? { stack } : {}) },
    },
    'error',
  );
}

/**
 * Run a telemetry write off the request path.
 *
 * `after()` lets Next flush the response first and still finish the write, which
 * matters because a floating promise in a route can be cut short. Outside a
 * request (unit tests, module scope) `after()` throws, so fall back to a
 * fire-and-forget call. Never throws either way.
 */
export function deferWrite(write: () => Promise<unknown>): void {
  const safe = () => write().catch(() => undefined);
  try {
    after(safe);
  } catch {
    void safe();
  }
}

/**
 * Record a route failure without making the caller wait or handle errors — the
 * form intended for `catch` blocks that are about to return a 5xx.
 */
export function noteRouteError(
  route: string,
  error: unknown,
  context: { roomName?: string; participantIdentity?: string } = {},
): void {
  deferWrite(() => reportRouteError(route, error, context));
}

/** Read the durable log, newest first. Throws if the log is unreachable. */
export async function listRunnerEvents(filters: {
  severity?: EventSeverity;
  room?: string;
  limit?: number;
}): Promise<ConciergeEvent[]> {
  const base = runnerBase();
  if (!base) throw new Error('BOT_RUNNER_URL is not configured — the event log is unreachable');

  const url = new URL(`${base}events`);
  if (filters.severity) url.searchParams.set('severity', filters.severity);
  if (filters.room) url.searchParams.set('room', filters.room);
  if (filters.limit) url.searchParams.set('limit', String(filters.limit));

  const res = await fetch(url.toString(), { headers: runnerHeaders(), cache: 'no-store' });
  if (!res.ok) throw new Error(`event log read failed (${res.status})`);
  const body = (await res.json()) as { events?: RunnerEventRow[] };
  return (body.events ?? []).map(fromRunnerRow);
}

/**
 * Severity for an incoming LiveKit webhook.
 *
 * Almost all of them are ordinary lifecycle noise, but a failed or aborted
 * egress means a recording was lost — for a study session that is an incident,
 * and it should stand out from the participant_joined stream.
 */
export function webhookSeverity(event: unknown): EventSeverity {
  const record = (typeof event === 'object' && event !== null ? event : {}) as Record<
    string,
    unknown
  >;
  const name = typeof record.event === 'string' ? record.event.toLowerCase() : '';
  const egressStatus = (record.egressInfo as { status?: unknown } | undefined)?.status;
  const status = typeof egressStatus === 'string' ? egressStatus.toUpperCase() : '';

  if (status.includes('FAILED') || status.includes('ABORTED')) return 'error';
  if (name.includes('failed')) return 'error';
  return 'info';
}
