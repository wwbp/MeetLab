import { beforeEach, describe, expect, it, vi } from 'vitest';

// The durable event log talks to agent-runner over HTTP; these are store tests.
vi.mock('@/lib/concierge/event-log', () => ({
  recordEvent: vi.fn().mockResolvedValue(undefined),
  deferWrite: (write: () => Promise<unknown>) => {
    void write().catch(() => undefined);
  },
}));

// Reset all in-memory store state before each test.
const STORE_KEYS = [
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
// events-store (now a façade over the durable event log)
// ---------------------------------------------------------------------------
describe('events-store', async () => {
  const { pushConciergeEvent } = await import('@/lib/concierge/events-store');
  const { recordEvent } = await import('@/lib/concierge/event-log');

  it('returns the event synchronously, with an id and a timestamp', () => {
    // Callers (notably the LiveKit webhook route) act on the returned event in
    // the same tick, so this stays synchronous even though the write is not.
    const ev = pushConciergeEvent({ source: 'concierge', event: 'test.event' });
    expect(ev.id).toBeTruthy();
    expect(ev.receivedAt).toBeTruthy();
    expect(ev.event).toBe('test.event');
    expect(ev.severity).toBe('info');
  });

  it('accepts custom receivedAt', () => {
    const ts = '2024-01-01T00:00:00.000Z';
    const ev = pushConciergeEvent({ source: 'webhook', event: 'e', receivedAt: ts });
    expect(ev.receivedAt).toBe(ts);
  });

  it('persists the event to the durable log', () => {
    pushConciergeEvent({ source: 'concierge', event: 'concierge.room.created', roomName: 'r' });
    expect(recordEvent).toHaveBeenCalledWith(
      expect.objectContaining({ event: 'concierge.room.created', roomName: 'r' }),
      'info',
    );
  });

  it('passes an explicit severity through', () => {
    pushConciergeEvent({ source: 'concierge', event: 'meet.route.error' }, 'error');
    expect(recordEvent).toHaveBeenCalledWith(expect.anything(), 'error');
  });

  it('still returns an event when the durable write fails', () => {
    // The room really was created; losing the telemetry must not look like failure.
    vi.mocked(recordEvent).mockRejectedValueOnce(new Error('runner down'));
    expect(() => pushConciergeEvent({ source: 'concierge', event: 'x' })).not.toThrow();
  });
});

