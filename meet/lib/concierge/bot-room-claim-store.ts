const STORE_KEY = '__concierge_bot_room_claim_store__';
const CLAIM_TTL_MS = 40 * 60 * 1000;

type BotRoomClaim = {
  roomName: string;
  botIdentity: string;
  claimedAt: number;
};

type BotRoomClaimStore = Map<string, BotRoomClaim>;

function getStore(): BotRoomClaimStore {
  const globalState = globalThis as typeof globalThis & { [STORE_KEY]?: BotRoomClaimStore };
  if (!globalState[STORE_KEY]) {
    globalState[STORE_KEY] = new Map<string, BotRoomClaim>();
  }
  return globalState[STORE_KEY];
}

function cleanupExpiredClaims(store: BotRoomClaimStore): void {
  const now = Date.now();
  for (const [roomName, claim] of store.entries()) {
    if (now - claim.claimedAt > CLAIM_TTL_MS) {
      store.delete(roomName);
    }
  }
}

export function getBotRoomClaim(roomName: string): BotRoomClaim | undefined {
  const store = getStore();
  cleanupExpiredClaims(store);
  return store.get(roomName);
}

export function claimBotRoom(roomName: string, botIdentity: string): boolean {
  const store = getStore();
  cleanupExpiredClaims(store);
  if (store.has(roomName)) {
    return false;
  }
  store.set(roomName, {
    roomName,
    botIdentity,
    claimedAt: Date.now(),
  });
  return true;
}

export function releaseBotRoomClaim(roomName: string): void {
  getStore().delete(roomName);
}
