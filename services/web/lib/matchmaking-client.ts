import { getServerConfig, requireEnv } from '@/lib/config/server';

/**
 * The runner's group start link endpoints (links + tickets), typed. Every meet route that
 * touches links or tickets goes through here; route tests vi.mock this module. The shapes
 * are the contract in tests/fixtures/matchmaking/*.json (the runner's tests read them too).
 * A reply is { status, body } as the runner sent it, so a route can map 403/404/410 itself;
 * an unreachable runner is a 502.
 */

/** @public for the start, waiting and console routes (lanes 3 and 4) */
export type TicketView =
  | { kind: 'searching'; found: number; size: number; expires_in_ms: number; requeued: boolean }
  | { kind: 'ready_check'; deadline_in_ms: number; pressed: boolean }
  | { kind: 'starting'; room: string | null; starts_in_ms: number }
  | { kind: 'expired' }
  | { kind: 'missed' }
  | { kind: 'done' };

/** @public for the start, waiting and console routes (lanes 3 and 4) */
export type TicketReply = {
  ticket: string;
  link_id: string;
  participant_id: string | null;
  view: TicketView;
};

/** @public for the start, waiting and console routes (lanes 3 and 4) */
export type TicketState =
  | 'searching'
  | 'found'
  | 'ready'
  | 'placed'
  | 'done'
  | 'expired'
  | 'removed';

/** @public for the start, waiting and console routes (lanes 3 and 4) */
export type LinkRow = {
  link_id: string;
  name: string;
  pool: string[];
  size: number;
  min_size: number;
  delay_s: number;
  max_wait_s: number;
  ready_check_s: number;
  has_password: boolean;
  active: boolean;
  legacy: boolean;
  created_at: string;
  stopped_at: string | null;
  counts: Record<TicketState, number>;
};

/** @public for the start, waiting and console routes (lanes 3 and 4) */
export type NewLink = {
  name: string;
  pool: string[];
  size: number;
  min_size: number;
  delay_s: number;
  max_wait_s: number;
  ready_check_s: number;
  password?: string;
};

/** @public for the start, waiting and console routes (lanes 3 and 4) */
export type NewTicket = {
  link_id: string;
  participant_id: string | null;
  password?: string;
  legacy?: { pool: string[] };
};

type RunnerError = { error: string };
/** @public for the start, waiting and console routes (lanes 3 and 4) */
export type RunnerReply<T> = { status: number; body: T | RunnerError };

async function call<T>(method: string, path: string, body?: object): Promise<RunnerReply<T>> {
  const config = getServerConfig();
  const base = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL').replace(/\/$/, '');
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (config.botRunnerSecret) headers.Authorization = `Bearer ${config.botRunnerSecret}`;
  try {
    const res = await fetch(`${base}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
    });
    return { status: res.status, body: await res.json() };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { status: 502, body: { error: `runner unreachable: ${message}` } };
  }
}

const id = encodeURIComponent;

export const createLink = (link: NewLink) => call<{ link_id: string }>('POST', '/links', link);
export const listLinks = () => call<{ links: LinkRow[] }>('GET', '/links');
export const setLinkActive = (linkId: string, active: boolean) =>
  call<{ link_id: string; active: boolean }>('PATCH', `/links/${id(linkId)}`, { active });
export const publicLink = (linkId: string) =>
  call<{ active: boolean; needs_password: boolean; size: number }>(
    'GET',
    `/links/${id(linkId)}/public`,
  );
export const createTicket = (ticket: NewTicket) => call<TicketReply>('POST', '/tickets', ticket);
export const pollTicket = (ticket: string) =>
  call<TicketReply>('POST', `/tickets/${id(ticket)}/poll`);
export const readyTicket = (ticket: string, participantId?: string) =>
  call<TicketReply>(
    'POST',
    `/tickets/${id(ticket)}/ready`,
    participantId ? { participant_id: participantId } : undefined,
  );
