import { deferWrite, recordEvent } from '@/lib/concierge/event-log';
import { randomId } from '@/lib/concierge/http-utils';
import type { ConciergeEvent, EventSeverity } from '@/lib/concierge/types';

/**
 * Concierge events, recorded durably.
 *
 * This used to be a capped in-memory ring keyed off `globalThis`, which meant
 * every admin action and webhook vanished on each deploy or restart — exactly
 * when you most want the history. Events now go to agent-runner's `events`
 * table, alongside the runner's and the bot's own mirrored warnings and errors.
 *
 * The function stays synchronous and still returns the event, because callers
 * (the LiveKit webhook route in particular) act on it in the same tick. The
 * write is deferred: `after()` runs it once the response is flushed, so nothing
 * on the request path waits on it, and a failed write cannot fail the request.
 */
export function pushConciergeEvent(
  event: Omit<ConciergeEvent, 'id' | 'receivedAt' | 'severity'> & { receivedAt?: string },
  severity: EventSeverity = 'info',
): ConciergeEvent {
  const entry: ConciergeEvent = {
    id: randomId(),
    receivedAt: event.receivedAt ?? new Date().toISOString(),
    severity,
    ...event,
  };

  deferWrite(() => recordEvent(event, severity));

  return entry;
}
