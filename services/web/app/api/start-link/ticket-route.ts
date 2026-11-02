import { NextResponse } from 'next/server';
import { completionCode } from '@/lib/completion-code';
import type { RunnerReply, TicketReply } from '@/lib/matchmaking-client';
import { checkRateLimit } from '@/lib/rate-limit';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// A waiting page polls every 2 s (30/min); keyed on the ticket, so people behind one IP
// (a lab, a campus NAT) never share a budget (R37).
const PER_TICKET_PER_MINUTE = 60;

/**
 * The status and Ready routes: a ticket token in a POST body → the runner → its view.
 * An expired ticket gets its wait code here (R36): the completion code's HMAC over
 * 'waited:<link>', so it is stable, recomputable by a researcher and never a room's code.
 */
export async function ticketRoute(
  request: Request,
  name: string,
  call: (ticket: string) => Promise<RunnerReply<TicketReply>>,
) {
  const body = await request.json().catch(() => ({}));
  const ticket = body?.ticket;
  if (typeof ticket !== 'string' || !UUID.test(ticket)) {
    return NextResponse.json({ error: 'Invalid ticket' }, { status: 400 });
  }
  const limit = checkRateLimit(`start-link-${name}:${ticket}`, PER_TICKET_PER_MINUTE, 60_000);
  if (!limit.allowed) {
    return NextResponse.json(
      { error: 'Too many requests' },
      { status: 429, headers: { 'Retry-After': String(Math.ceil(limit.retryAfterMs / 1000)) } },
    );
  }
  const { status, body: reply } = await call(ticket);
  if (status !== 200 || !('view' in reply)) return NextResponse.json(reply, { status });
  if (reply.view.kind !== 'expired' || !reply.participant_id) return NextResponse.json(reply);
  const code = completionCode(
    `waited:${reply.link_id}`,
    reply.participant_id,
    process.env.LIVEKIT_API_SECRET ?? '',
  );
  return NextResponse.json({ ...reply, view: { kind: 'expired', code } });
}
