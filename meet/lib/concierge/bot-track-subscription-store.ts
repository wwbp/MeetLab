const STORE_KEY = '__concierge_bot_track_subscription_store__';
const MAX_SIGNALS = 300;

export type BotTrackSubscriptionSignal = {
  roomName: string;
  botIdentity?: string;
  trackSid?: string;
  sourceEvent: string;
  observedAt: string;
};

type BotTrackSubscriptionStore = BotTrackSubscriptionSignal[];

function getStore(): BotTrackSubscriptionStore {
  const globalState = globalThis as typeof globalThis & { [STORE_KEY]?: BotTrackSubscriptionStore };
  if (!globalState[STORE_KEY]) {
    globalState[STORE_KEY] = [];
  }
  return globalState[STORE_KEY];
}

export function recordBotTrackSubscriptionSignal(
  signal: Omit<BotTrackSubscriptionSignal, 'observedAt'> & { observedAt?: string }
): BotTrackSubscriptionSignal {
  const entry: BotTrackSubscriptionSignal = {
    observedAt: signal.observedAt ?? new Date().toISOString(),
    ...signal,
  };
  const store = getStore();
  store.unshift(entry);
  if (store.length > MAX_SIGNALS) {
    store.length = MAX_SIGNALS;
  }
  return entry;
}

export function getLatestBotTrackSubscriptionSignal(
  roomName: string,
  botIdentity?: string
): BotTrackSubscriptionSignal | undefined {
  return getStore().find((signal) => {
    if (signal.roomName !== roomName) {
      return false;
    }
    if (!botIdentity) {
      return true;
    }
    return signal.botIdentity === botIdentity;
  });
}

export function clearBotTrackSubscriptionSignalsForRoom(roomName: string): void {
  const store = getStore();
  const filtered = store.filter((signal) => signal.roomName !== roomName);
  store.length = 0;
  store.push(...filtered);
}
