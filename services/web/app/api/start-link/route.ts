import { NextResponse } from 'next/server';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { createTicket, publicLink } from '@/lib/matchmaking-client';
import { checkRateLimit, getClientIp } from '@/lib/rate-limit';
import { resolveLinkParam } from '@/lib/start-link';
import { normalizeProlificId } from '@/lib/study';
import { COPY } from '@/lib/waiting';
import { noteRouteError } from '@/lib/concierge/event-log';

export const dynamic = 'force-dynamic';

// Each ticket can start a room + bot — keep the public window tight. Per IP and link,
// 5 × the link's room size a minute (size 1 = today's 5), so a lab full of people behind
// one IP can fill a group (R37). Tunable: START_LINK_MAX_PER_MINUTE. The IP is the last
// X-Forwarded-For entry, the one the ALB appended (lib/rate-limit.ts).
const RATE_LIMIT_MAX = Number(process.env.START_LINK_MAX_PER_MINUTE) || 5;
const WINDOW_MS = 60_000;

// R29: 5 wrong passwords in a minute from one IP for one link refuse that IP for a minute.
// In memory per task, like lib/rate-limit.ts.
// ponytail: entries are never evicted; a few bytes per (IP, link) that ever got one wrong.
const WRONG_MAX = 5;
type Wrong = Map<string, { count: number; start: number; until: number }>;
const g = globalThis as { __meetlab_start_link_wrong__?: Wrong };
const wrong = (g.__meetlab_start_link_wrong__ ??= new Map());

function noteWrong(key: string, now: number) {
  const e = wrong.get(key);
  const entry = !e || now - e.start >= WINDOW_MS ? { count: 0, start: now, until: 0 } : e;
  entry.count += 1;
  if (entry.count >= WRONG_MAX) entry.until = now + WINDOW_MS;
  wrong.set(key, entry);
}

const error = (message: string, status: number) =>
  NextResponse.json({ error: message }, { status });

/**
 * Public endpoint: Join on a start link → a ticket in the link's queue (the runner's
 * matchmaker). Size 1 with no start delay: the room is set up before the reply, which is
 * today's {url, roomName}. Otherwise {ticket, view} for the waiting page. POST-only so link
 * unfurlers/prefetchers (which GET) never provision anything.
 */
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  const link = await resolveLinkParam(typeof body?.token === 'string' ? body.token : '');
  if (!link) return error('Invalid start link', 400);

  // An old JWT link is always size 1 without a password (R3); a stored one says its size.
  let size = 1;
  if (!link.legacyPool) {
    const info = await publicLink(link.linkId);
    if (info.status === 404) return error('Invalid start link', 400);
    if (info.status !== 200 || !('size' in info.body))
      return error('Could not reach the study', 502);
    size = info.body.size;
  }

  // Size 1: the ID is asked on pre-join, as today, and each click is a new one-off ticket.
  const typed = typeof body?.participant_id === 'string' ? body.participant_id : null;
  const participantId = size > 1 ? normalizeProlificId(typed) : null;
  if (size > 1 && !participantId) return error('Enter your Prolific ID', 400);

  const ip = getClientIp(request);
  const key = `${ip}:${link.linkId}`;
  const now = Date.now();
  if ((wrong.get(key)?.until ?? 0) > now) return error(COPY.locked, 429);
  const limit = checkRateLimit(`start-link:${key}`, RATE_LIMIT_MAX * size, WINDOW_MS);
  if (!limit.allowed) {
    return NextResponse.json(
      { error: 'Too many meetings started — try again shortly' },
      { status: 429, headers: { 'Retry-After': String(Math.ceil(limit.retryAfterMs / 1000)) } },
    );
  }

  const res = await createTicket({
    link_id: link.linkId,
    participant_id: participantId,
    ...(typeof body?.password === 'string' && body.password ? { password: body.password } : {}),
    ...(link.legacyPool ? { legacy: { pool: link.legacyPool } } : {}),
  });
  if (res.status === 403) {
    noteWrong(key, now);
    return error(COPY.wrongPassword, 403);
  }
  if (res.status === 410) return error(COPY.closedHeading, 410);
  if (res.status === 404) return error('Invalid start link', 400);
  if (res.status !== 200 || !('view' in res.body)) {
    noteRouteError('POST /api/start-link', new Error(JSON.stringify(res.body)));
    return error('Failed to start the meeting', 502);
  }

  const { view } = res.body;
  if (size === 1 && view.kind === 'starting' && view.starts_in_ms <= 0) {
    // The runner awaited setup; no room means it failed. A retry click retries it (R11).
    if (!view.room) return error('Failed to start the meeting', 502);
    pushConciergeEvent({
      source: 'concierge',
      event: 'concierge.bot.started',
      roomName: view.room,
      payload: { via: 'start-link', linkId: link.linkId },
    });
    return NextResponse.json({
      url: `/rooms/${encodeURIComponent(view.room)}`,
      roomName: view.room,
    });
  }
  return NextResponse.json(res.body);
}
