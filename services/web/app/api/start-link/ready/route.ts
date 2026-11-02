import { readyTicket } from '@/lib/matchmaking-client';
import { ticketRoute } from '../ticket-route';

export const dynamic = 'force-dynamic';

/** Public: the ready check's "I'm ready" button. */
export const POST = (request: Request) => ticketRoute(request, 'ready', readyTicket);
