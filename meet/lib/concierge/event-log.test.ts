import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockFetch = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());

vi.stubGlobal('fetch', mockFetch);
vi.mock('@/lib/config/server', () => ({ getServerConfig: mockGetServerConfig }));

import {
  fromRunnerRow,
  listRunnerEvents,
  recordEvent,
  reportRouteError,
  toRunnerEvent,
  webhookSeverity,
} from './event-log';

const CONFIG = { botRunnerUrl: 'http://agent-runner:7860/', botRunnerSecret: 'test-secret' };

const row = (over: Record<string, unknown> = {}) => ({
  id: 12,
  type: 'concierge.room.created',
  severity: 'info',
  room_name: 'room-1',
  conv_id: null,
  payload: {},
  created_at: '2026-07-29T21:00:00+00:00',
  ...over,
});

describe('toRunnerEvent', () => {
  it('maps a concierge event onto the runner event contract', () => {
    const body = toRunnerEvent({
      source: 'concierge',
      event: 'concierge.room.created',
      roomName: 'room-1',
    });
    expect(body.type).toBe('concierge.room.created');
    expect(body.room_name).toBe('room-1');
    expect(body.severity).toBe('info');
  });

  it('keeps source and participant in the payload, not the routing columns', () => {
    const body = toRunnerEvent({
      source: 'webhook',
      event: 'participant_joined',
      roomName: 'room-1',
      participantIdentity: 'ann__b2',
      payload: { extra: 1 },
    });
    expect(body.payload).toEqual({ source: 'webhook', participant_identity: 'ann__b2', extra: 1 });
  });

  it('carries an explicit severity through', () => {
    expect(toRunnerEvent({ source: 'concierge', event: 'x' }, 'error').severity).toBe('error');
  });

  it('nests a non-object payload rather than dropping it', () => {
    const body = toRunnerEvent({ source: 'concierge', event: 'x', payload: 'a string' });
    expect(body.payload).toEqual({ source: 'concierge', data: 'a string' });
  });

  it('omits an absent room instead of sending null', () => {
    expect(toRunnerEvent({ source: 'concierge', event: 'x' }).room_name).toBeUndefined();
  });
});

describe('fromRunnerRow', () => {
  it('maps a stored row to the shape the console renders', () => {
    const event = fromRunnerRow(row());
    expect(event).toMatchObject({
      id: '12',
      event: 'concierge.room.created',
      severity: 'info',
      roomName: 'room-1',
      receivedAt: '2026-07-29T21:00:00+00:00',
    });
  });

  it('recovers source from the payload when meet wrote it', () => {
    expect(fromRunnerRow(row({ payload: { source: 'webhook' } })).source).toBe('webhook');
  });

  it('infers source from the event type when nothing wrote one', () => {
    // Errors mirrored from the runner and the bot arrive with no source key.
    expect(fromRunnerRow(row({ type: 'bot.log.error' })).source).toBe('bot');
    expect(fromRunnerRow(row({ type: 'runner.log.warning' })).source).toBe('runner');
    expect(fromRunnerRow(row({ type: 'concierge.bot.started' })).source).toBe('concierge');
    // LiveKit's own webhook types are bare words.
    expect(fromRunnerRow(row({ type: 'participant_joined' })).source).toBe('webhook');
  });

  it('does not blame LiveKit for an event it did not send', () => {
    // Dotted but unrecognised: written straight to the runner's API by something
    // that did not name itself. Labelling it 'webhook' would be a false trail.
    expect(fromRunnerRow(row({ type: 'something.custom' })).source).toBe('runner');
  });

  it('lifts participant identity out of the payload', () => {
    const event = fromRunnerRow(row({ payload: { participant_identity: 'ann__b2', keep: 1 } }));
    expect(event.participantIdentity).toBe('ann__b2');
    expect(event.payload).toEqual({ keep: 1 });
  });

  it('defaults severity to info for rows written before the column existed', () => {
    expect(fromRunnerRow(row({ severity: undefined })).severity).toBe('info');
  });

  it('survives a null payload', () => {
    const event = fromRunnerRow(row({ payload: null }));
    expect(event.payload).toEqual({});
    expect(event.participantIdentity).toBeUndefined();
  });
});

describe('recordEvent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(CONFIG);
    mockFetch.mockResolvedValue(new Response(null, { status: 202 }));
  });

  it('posts to the runner with the shared secret', async () => {
    await recordEvent({ source: 'concierge', event: 'concierge.room.created', roomName: 'r' });
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe('http://agent-runner:7860/events');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer test-secret');
    expect(JSON.parse(init.body as string).type).toBe('concierge.room.created');
  });

  // An event write describes an operation; it must never be the thing that
  // breaks it. Every one of these used to be a 500 waiting to happen.
  it('never throws when the runner is unreachable', async () => {
    mockFetch.mockRejectedValue(new Error('ECONNREFUSED'));
    await expect(recordEvent({ source: 'concierge', event: 'x' })).resolves.toBeUndefined();
  });

  it('never throws when the runner rejects the write', async () => {
    mockFetch.mockResolvedValue(new Response('bad', { status: 400 }));
    await expect(recordEvent({ source: 'concierge', event: 'x' })).resolves.toBeUndefined();
  });

  it('does nothing when no runner is configured', async () => {
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: undefined });
    await recordEvent({ source: 'concierge', event: 'x' });
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe('reportRouteError', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(CONFIG);
    mockFetch.mockResolvedValue(new Response(null, { status: 202 }));
  });

  it('records an error event naming the route and the failure', async () => {
    await reportRouteError('POST /api/concierge/rooms', new Error('livekit exploded'), {
      roomName: 'room-1',
    });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body as string);
    expect(body.severity).toBe('error');
    expect(body.type).toBe('meet.route.error');
    expect(body.room_name).toBe('room-1');
    expect(body.payload.route).toBe('POST /api/concierge/rooms');
    expect(body.payload.message).toBe('livekit exploded');
  });

  it('handles a thrown non-Error', async () => {
    await reportRouteError('GET /x', 'just a string');
    const body = JSON.parse(mockFetch.mock.calls[0][1].body as string);
    expect(body.payload.message).toBe('just a string');
  });

  it('truncates a huge stack instead of posting a novel', async () => {
    const error = new Error('boom');
    error.stack = 'x'.repeat(20_000);
    await reportRouteError('GET /x', error);
    const body = JSON.parse(mockFetch.mock.calls[0][1].body as string);
    expect(body.payload.stack.length).toBeLessThanOrEqual(2_048);
  });

  it('never throws, even if reporting itself fails', async () => {
    mockFetch.mockRejectedValue(new Error('down'));
    await expect(reportRouteError('GET /x', new Error('boom'))).resolves.toBeUndefined();
  });
});

describe('listRunnerEvents', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(CONFIG);
    mockFetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          events: [row(), row({ id: 13, type: 'bot.log.error', severity: 'error' })],
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );
  });

  it('reads the durable log and maps every row', async () => {
    const events = await listRunnerEvents({});
    expect(events).toHaveLength(2);
    expect(events[1]).toMatchObject({ source: 'bot', severity: 'error' });
  });

  it('passes filters through to the runner', async () => {
    await listRunnerEvents({ severity: 'error', room: 'room-1', limit: 25 });
    const url = new URL(mockFetch.mock.calls[0][0] as string);
    expect(url.pathname).toBe('/events');
    expect(url.searchParams.get('severity')).toBe('error');
    expect(url.searchParams.get('room')).toBe('room-1');
    expect(url.searchParams.get('limit')).toBe('25');
  });

  it('omits filters that were not asked for', async () => {
    await listRunnerEvents({});
    const url = new URL(mockFetch.mock.calls[0][0] as string);
    expect(url.searchParams.get('severity')).toBeNull();
    expect(url.searchParams.get('room')).toBeNull();
  });

  // Unlike writing, reading must fail loudly: a console showing an empty list
  // when the log is unreachable would read as "nothing went wrong".
  it('throws when the runner is unreachable', async () => {
    mockFetch.mockRejectedValue(new Error('ECONNREFUSED'));
    await expect(listRunnerEvents({})).rejects.toThrow();
  });

  it('throws when the runner errors', async () => {
    mockFetch.mockResolvedValue(new Response('nope', { status: 500 }));
    await expect(listRunnerEvents({})).rejects.toThrow();
  });

  it('throws when no runner is configured', async () => {
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: undefined });
    await expect(listRunnerEvents({})).rejects.toThrow();
  });
});

describe('webhookSeverity', () => {
  it('treats ordinary lifecycle webhooks as info', () => {
    expect(webhookSeverity({ event: 'participant_joined' })).toBe('info');
    expect(
      webhookSeverity({ event: 'egress_ended', egressInfo: { status: 'EGRESS_COMPLETE' } }),
    ).toBe('info');
  });

  it('flags a lost recording as an error', () => {
    // A failed egress means a study session's recording is gone; that cannot
    // look the same as a participant joining.
    expect(
      webhookSeverity({ event: 'egress_ended', egressInfo: { status: 'EGRESS_FAILED' } }),
    ).toBe('error');
    expect(
      webhookSeverity({ event: 'egress_ended', egressInfo: { status: 'EGRESS_ABORTED' } }),
    ).toBe('error');
    expect(webhookSeverity({ event: 'track_publish_failed' })).toBe('error');
  });

  it('survives junk', () => {
    expect(webhookSeverity(null)).toBe('info');
    expect(webhookSeverity({})).toBe('info');
    expect(webhookSeverity({ event: 42, egressInfo: 'nope' })).toBe('info');
  });
});
