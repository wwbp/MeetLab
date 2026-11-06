import { readyTicket } from '@/lib/matchmaking-client';
import { normalizeProlificId } from '@/lib/study';
import { ticketRoute } from '../ticket-route';

export const dynamic = 'force-dynamic';

/** Public: the ready check's "I'm ready" button, with the Prolific ID if one was typed. */
export const POST = (request: Request) =>
  ticketRoute(request, 'ready', async (ticket, body) => {
    if (body.participant_id === undefined) return readyTicket(ticket, undefined);
    const id = normalizeProlificId(
      typeof body.participant_id === 'string' ? body.participant_id : null,
    );
    if (!id) return { status: 400, body: { error: 'Enter your Prolific ID' } };
    return readyTicket(ticket, id);
  });
