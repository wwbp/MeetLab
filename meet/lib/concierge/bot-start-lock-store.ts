const STORE_KEY = '__concierge_bot_start_lock_store__';

type BotStartLockStore = Set<string>;

function getStore(): BotStartLockStore {
  const globalState = globalThis as typeof globalThis & { [STORE_KEY]?: BotStartLockStore };
  if (!globalState[STORE_KEY]) {
    globalState[STORE_KEY] = new Set<string>();
  }
  return globalState[STORE_KEY];
}

export function acquireBotStartLock(roomName: string): boolean {
  const store = getStore();
  if (store.has(roomName)) {
    return false;
  }
  store.add(roomName);
  return true;
}

export function releaseBotStartLock(roomName: string): void {
  getStore().delete(roomName);
}
