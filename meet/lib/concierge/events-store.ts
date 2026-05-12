import type { ConciergeEvent } from '@/lib/concierge/types';
import { randomId } from '@/lib/concierge/http-utils';

const MAX_EVENTS = 250;
const STORE_KEY = '__concierge_events_store__';

type ConciergeStore = ConciergeEvent[];

function getStore(): ConciergeStore {
  const globalState = globalThis as typeof globalThis & { [STORE_KEY]?: ConciergeStore };
  if (!globalState[STORE_KEY]) {
    globalState[STORE_KEY] = [];
  }
  return globalState[STORE_KEY];
}

export function pushConciergeEvent(
  event: Omit<ConciergeEvent, 'id' | 'receivedAt'> & { receivedAt?: string }
): ConciergeEvent {
  const entry: ConciergeEvent = {
    id: randomId(),
    receivedAt: event.receivedAt ?? new Date().toISOString(),
    ...event,
  };

  const store = getStore();
  store.unshift(entry);
  if (store.length > MAX_EVENTS) {
    store.length = MAX_EVENTS;
  }

  return entry;
}

export function listConciergeEvents(limit = 50): ConciergeEvent[] {
  const boundedLimit = Number.isFinite(limit) ? Math.min(Math.max(limit, 1), 200) : 50;
  return getStore().slice(0, boundedLimit);
}
