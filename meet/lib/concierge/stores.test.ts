import { beforeEach, describe, expect, it, vi } from 'vitest';

// Reset all in-memory store state before each test.
const STORE_KEYS = [
  '__concierge_bot_request_store__',
  '__concierge_bot_room_claim_store__',
  '__concierge_bot_start_lock_store__',
  '__concierge_bot_track_subscription_store__',
  '__concierge_events_store__',
  '__concierge_room_presence_store__',
];
beforeEach(() => {
  for (const key of STORE_KEYS) {
    (globalThis as Record<string, unknown>)[key] = undefined;
  }
});

// ---------------------------------------------------------------------------
// bot-requests-store
// ---------------------------------------------------------------------------
describe('bot-requests-store', async () => {
  const { addBotRequest, listBotRequestsForRoom } = await import(
    '@/lib/concierge/bot-requests-store'
  );

  it('addBotRequest assigns id and requestedAt, then retrieves by room', () => {
    const req = addBotRequest({ roomName: 'room-a', status: 'started' });
    expect(req.id).toBeTruthy();
    expect(req.requestedAt).toBeTruthy();
    expect(req.roomName).toBe('room-a');
    expect(req.status).toBe('started');

    const list = listBotRequestsForRoom('room-a');
    expect(list).toHaveLength(1);
    expect(list[0].id).toBe(req.id);
  });

  it('listBotRequestsForRoom filters by room', () => {
    addBotRequest({ roomName: 'room-a', status: 'started' });
    addBotRequest({ roomName: 'room-b', status: 'failed', error: 'oops' });
    expect(listBotRequestsForRoom('room-a')).toHaveLength(1);
    expect(listBotRequestsForRoom('room-b')).toHaveLength(1);
    expect(listBotRequestsForRoom('room-c')).toHaveLength(0);
  });

  it('listBotRequestsForRoom respects limit', () => {
    for (let i = 0; i < 10; i++) {
      addBotRequest({ roomName: 'room-a', status: 'started' });
    }
    expect(listBotRequestsForRoom('room-a', 3)).toHaveLength(3);
  });

  it('listBotRequestsForRoom clamps limit to [1, 100]', () => {
    for (let i = 0; i < 5; i++) addBotRequest({ roomName: 'r', status: 'started' });
    expect(listBotRequestsForRoom('r', 0)).toHaveLength(1);
    expect(listBotRequestsForRoom('r', 1000)).toHaveLength(5);
  });

  it('accepts custom requestedAt', () => {
    const ts = '2024-01-01T00:00:00.000Z';
    const req = addBotRequest({ roomName: 'r', status: 'started', requestedAt: ts });
    expect(req.requestedAt).toBe(ts);
  });

  it('caps store at MAX_REQUESTS (300)', () => {
    for (let i = 0; i < 310; i++) addBotRequest({ roomName: 'r', status: 'started' });
    const all = listBotRequestsForRoom('r', 100);
    expect(all.length).toBeLessThanOrEqual(100);
  });
});

// ---------------------------------------------------------------------------
// bot-room-claim-store
// ---------------------------------------------------------------------------
describe('bot-room-claim-store', async () => {
  const { claimBotRoom, getBotRoomClaim, releaseBotRoomClaim } = await import(
    '@/lib/concierge/bot-room-claim-store'
  );

  it('claimBotRoom succeeds when no existing claim', () => {
    expect(claimBotRoom('room-a', 'bot_1')).toBe(true);
  });

  it('getBotRoomClaim returns the claim after claiming', () => {
    claimBotRoom('room-a', 'bot_1');
    const claim = getBotRoomClaim('room-a');
    expect(claim).toBeDefined();
    expect(claim?.botIdentity).toBe('bot_1');
    expect(claim?.roomName).toBe('room-a');
  });

  it('getBotRoomClaim returns undefined for unknown room', () => {
    expect(getBotRoomClaim('no-such-room')).toBeUndefined();
  });

  it('claimBotRoom fails when room already claimed', () => {
    claimBotRoom('room-a', 'bot_1');
    expect(claimBotRoom('room-a', 'bot_2')).toBe(false);
  });

  it('releaseBotRoomClaim clears the claim', () => {
    claimBotRoom('room-a', 'bot_1');
    releaseBotRoomClaim('room-a');
    expect(getBotRoomClaim('room-a')).toBeUndefined();
  });

  it('can reclaim after release', () => {
    claimBotRoom('room-a', 'bot_1');
    releaseBotRoomClaim('room-a');
    expect(claimBotRoom('room-a', 'bot_2')).toBe(true);
    expect(getBotRoomClaim('room-a')?.botIdentity).toBe('bot_2');
  });

  it('expired claims are cleaned up (TTL = 40 min)', () => {
    const now = Date.now();
    // Set claimedAt to 41 minutes ago (past the 40-min TTL).
    (globalThis as Record<string, unknown>)['__concierge_bot_room_claim_store__'] = new Map([
      ['room-a', { roomName: 'room-a', botIdentity: 'bot_old', claimedAt: now - 41 * 60 * 1000 }],
    ]);
    expect(getBotRoomClaim('room-a')).toBeUndefined();
  });

  it('claims within TTL are retained', () => {
    const now = Date.now();
    (globalThis as Record<string, unknown>)['__concierge_bot_room_claim_store__'] = new Map([
      ['room-a', { roomName: 'room-a', botIdentity: 'bot_live', claimedAt: now - 5 * 60 * 1000 }],
    ]);
    expect(getBotRoomClaim('room-a')?.botIdentity).toBe('bot_live');
  });
});

// ---------------------------------------------------------------------------
// bot-start-lock-store
// ---------------------------------------------------------------------------
describe('bot-start-lock-store', async () => {
  const { acquireBotStartLock, releaseBotStartLock } = await import(
    '@/lib/concierge/bot-start-lock-store'
  );

  it('acquireBotStartLock succeeds when no lock held', () => {
    expect(acquireBotStartLock('room-a')).toBe(true);
  });

  it('acquireBotStartLock fails when lock already held', () => {
    acquireBotStartLock('room-a');
    expect(acquireBotStartLock('room-a')).toBe(false);
  });

  it('releaseBotStartLock allows re-acquisition', () => {
    acquireBotStartLock('room-a');
    releaseBotStartLock('room-a');
    expect(acquireBotStartLock('room-a')).toBe(true);
  });

  it('locks are per-room', () => {
    acquireBotStartLock('room-a');
    expect(acquireBotStartLock('room-b')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// bot-track-subscription-store
// ---------------------------------------------------------------------------
describe('bot-track-subscription-store', async () => {
  const {
    clearBotTrackSubscriptionSignalsForRoom,
    getLatestBotTrackSubscriptionSignal,
    recordBotTrackSubscriptionSignal,
  } = await import('@/lib/concierge/bot-track-subscription-store');

  it('recordBotTrackSubscriptionSignal stores and returns signal with observedAt', () => {
    const signal = recordBotTrackSubscriptionSignal({
      roomName: 'room-a',
      botIdentity: 'bot_1',
      trackSid: 'TR_abc',
      sourceEvent: 'track_subscribed',
    });
    expect(signal.observedAt).toBeTruthy();
    expect(signal.roomName).toBe('room-a');
  });

  it('getLatestBotTrackSubscriptionSignal returns most recent for room', () => {
    recordBotTrackSubscriptionSignal({ roomName: 'room-a', sourceEvent: 'e1' });
    recordBotTrackSubscriptionSignal({ roomName: 'room-a', sourceEvent: 'e2' });
    const latest = getLatestBotTrackSubscriptionSignal('room-a');
    expect(latest?.sourceEvent).toBe('e2');
  });

  it('getLatestBotTrackSubscriptionSignal filters by botIdentity when provided', () => {
    recordBotTrackSubscriptionSignal({ roomName: 'r', botIdentity: 'bot_x', sourceEvent: 'e1' });
    recordBotTrackSubscriptionSignal({ roomName: 'r', botIdentity: 'bot_y', sourceEvent: 'e2' });
    expect(getLatestBotTrackSubscriptionSignal('r', 'bot_x')?.botIdentity).toBe('bot_x');
    expect(getLatestBotTrackSubscriptionSignal('r', 'bot_y')?.botIdentity).toBe('bot_y');
  });

  it('returns undefined for unknown room', () => {
    expect(getLatestBotTrackSubscriptionSignal('no-room')).toBeUndefined();
  });

  it('clearBotTrackSubscriptionSignalsForRoom removes only that room', () => {
    recordBotTrackSubscriptionSignal({ roomName: 'room-a', sourceEvent: 'e' });
    recordBotTrackSubscriptionSignal({ roomName: 'room-b', sourceEvent: 'e' });
    clearBotTrackSubscriptionSignalsForRoom('room-a');
    expect(getLatestBotTrackSubscriptionSignal('room-a')).toBeUndefined();
    expect(getLatestBotTrackSubscriptionSignal('room-b')).toBeDefined();
  });

  it('accepts custom observedAt', () => {
    const ts = '2024-06-01T00:00:00.000Z';
    const sig = recordBotTrackSubscriptionSignal({ roomName: 'r', sourceEvent: 'e', observedAt: ts });
    expect(sig.observedAt).toBe(ts);
  });
});

// ---------------------------------------------------------------------------
// events-store
// ---------------------------------------------------------------------------
describe('events-store', async () => {
  const { listConciergeEvents, pushConciergeEvent } = await import(
    '@/lib/concierge/events-store'
  );

  it('pushConciergeEvent assigns id and receivedAt', () => {
    const ev = pushConciergeEvent({ source: 'concierge', event: 'test.event' });
    expect(ev.id).toBeTruthy();
    expect(ev.receivedAt).toBeTruthy();
    expect(ev.event).toBe('test.event');
  });

  it('listConciergeEvents returns most recent events first', () => {
    pushConciergeEvent({ source: 'concierge', event: 'first' });
    pushConciergeEvent({ source: 'concierge', event: 'second' });
    const events = listConciergeEvents(2);
    expect(events[0].event).toBe('second');
    expect(events[1].event).toBe('first');
  });

  it('listConciergeEvents respects limit', () => {
    for (let i = 0; i < 10; i++) pushConciergeEvent({ source: 'concierge', event: `e${i}` });
    expect(listConciergeEvents(3)).toHaveLength(3);
  });

  it('listConciergeEvents clamps limit to [1, 200]', () => {
    for (let i = 0; i < 5; i++) pushConciergeEvent({ source: 'concierge', event: `e${i}` });
    expect(listConciergeEvents(0)).toHaveLength(1);
    expect(listConciergeEvents(9999)).toHaveLength(5);
  });

  it('accepts custom receivedAt', () => {
    const ts = '2024-01-01T00:00:00.000Z';
    const ev = pushConciergeEvent({ source: 'webhook', event: 'e', receivedAt: ts });
    expect(ev.receivedAt).toBe(ts);
  });
});

// ---------------------------------------------------------------------------
// participant-presence-store
// ---------------------------------------------------------------------------
describe('participant-presence-store', async () => {
  const { diffRoomPresence } = await import('@/lib/concierge/participant-presence-store');

  it('first call treats all identities as joined', () => {
    const { joined, left } = diffRoomPresence('room-a', ['alice', 'bob']);
    expect(joined).toEqual(expect.arrayContaining(['alice', 'bob']));
    expect(left).toHaveLength(0);
  });

  it('second call with same identities produces no diff', () => {
    diffRoomPresence('room-a', ['alice', 'bob']);
    const { joined, left } = diffRoomPresence('room-a', ['alice', 'bob']);
    expect(joined).toHaveLength(0);
    expect(left).toHaveLength(0);
  });

  it('detects newly joined identity', () => {
    diffRoomPresence('room-a', ['alice']);
    const { joined, left } = diffRoomPresence('room-a', ['alice', 'carol']);
    expect(joined).toContain('carol');
    expect(left).toHaveLength(0);
  });

  it('detects departed identity', () => {
    diffRoomPresence('room-a', ['alice', 'bob']);
    const { joined, left } = diffRoomPresence('room-a', ['alice']);
    expect(left).toContain('bob');
    expect(joined).toHaveLength(0);
  });

  it('handles join and leave simultaneously', () => {
    diffRoomPresence('room-a', ['alice', 'bob']);
    const { joined, left } = diffRoomPresence('room-a', ['alice', 'carol']);
    expect(joined).toContain('carol');
    expect(left).toContain('bob');
  });

  it('tracks rooms independently', () => {
    diffRoomPresence('room-a', ['alice']);
    diffRoomPresence('room-b', ['bob']);
    const { joined: ja, left: la } = diffRoomPresence('room-a', []);
    expect(la).toContain('alice');
    expect(ja).toHaveLength(0);
    // room-b unaffected
    const { joined: jb, left: lb } = diffRoomPresence('room-b', ['bob']);
    expect(jb).toHaveLength(0);
    expect(lb).toHaveLength(0);
  });
});
