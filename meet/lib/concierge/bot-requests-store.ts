import type { ConciergeBotRequest } from '@/lib/concierge/types';
import { randomId } from '@/lib/concierge/http-utils';

const MAX_REQUESTS = 300;
const STORE_KEY = '__concierge_bot_request_store__';

type BotRequestStore = ConciergeBotRequest[];

function getStore(): BotRequestStore {
  const globalState = globalThis as typeof globalThis & { [STORE_KEY]?: BotRequestStore };
  if (!globalState[STORE_KEY]) {
    globalState[STORE_KEY] = [];
  }
  return globalState[STORE_KEY];
}

export function addBotRequest(
  request: Omit<ConciergeBotRequest, 'id' | 'requestedAt'> & { requestedAt?: string }
): ConciergeBotRequest {
  const entry: ConciergeBotRequest = {
    id: randomId(),
    requestedAt: request.requestedAt ?? new Date().toISOString(),
    ...request,
  };
  const store = getStore();
  store.unshift(entry);
  if (store.length > MAX_REQUESTS) {
    store.length = MAX_REQUESTS;
  }
  return entry;
}

export function listBotRequestsForRoom(roomName: string, limit = 30): ConciergeBotRequest[] {
  const boundedLimit = Number.isFinite(limit) ? Math.min(Math.max(limit, 1), 100) : 30;
  return getStore()
    .filter((request) => request.roomName === roomName)
    .slice(0, boundedLimit);
}
