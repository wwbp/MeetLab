import { pollTicket } from '@/lib/matchmaking-client';
import { ticketRoute } from '../ticket-route';

export const dynamic = 'force-dynamic';

/** Public: the waiting page's poll. Marks the ticket live and moves its match along. */
export const POST = (request: Request) =>
  ticketRoute(request, 'status', (ticket) => pollTicket(ticket));
