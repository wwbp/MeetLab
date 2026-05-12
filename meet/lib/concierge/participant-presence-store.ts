const STORE_KEY = '__concierge_room_presence_store__';

type RoomPresenceStore = Record<string, Set<string>>;

function getStore(): RoomPresenceStore {
  const globalState = globalThis as typeof globalThis & { [STORE_KEY]?: RoomPresenceStore };
  if (!globalState[STORE_KEY]) {
    globalState[STORE_KEY] = {};
  }
  return globalState[STORE_KEY];
}

export function diffRoomPresence(
  roomName: string,
  currentIdentities: string[]
): {
  joined: string[];
  left: string[];
} {
  const store = getStore();
  const previous = store[roomName] ?? new Set<string>();
  const current = new Set(currentIdentities);

  const joined = [...current].filter((identity) => !previous.has(identity));
  const left = [...previous].filter((identity) => !current.has(identity));

  store[roomName] = current;
  return { joined, left };
}
