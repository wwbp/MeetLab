import { describe, expect, it } from 'vitest';
import {
  isBotParticipant,
  mapParticipant,
  mapRoom,
  mapTrack,
  mapWebhookEvent,
  toHttpLiveKitUrl,
} from './livekit-admin';

// ---------------------------------------------------------------------------
// toHttpLiveKitUrl
// ---------------------------------------------------------------------------
describe('toHttpLiveKitUrl', () => {
  it('converts ws: to http:', () => {
    expect(toHttpLiveKitUrl('ws://transport-server:7880')).toBe('http://transport-server:7880/');
  });

  it('converts wss: to https:', () => {
    expect(toHttpLiveKitUrl('wss://my.livekit.cloud')).toBe('https://my.livekit.cloud/');
  });

  it('leaves http: unchanged', () => {
    expect(toHttpLiveKitUrl('http://transport-server:7880')).toBe('http://transport-server:7880/');
  });

  it('preserves path and port', () => {
    expect(toHttpLiveKitUrl('ws://host:1234/path')).toBe('http://host:1234/path');
  });
});

// ---------------------------------------------------------------------------
// isBotParticipant
// ---------------------------------------------------------------------------
describe('isBotParticipant', () => {
  it('returns true for identity starting with bot_', () => {
    expect(isBotParticipant({ identity: 'bot_room_abc123' })).toBe(true);
  });

  it('returns true for name "assistant" (case-insensitive)', () => {
    expect(isBotParticipant({ identity: 'other', name: 'Assistant' })).toBe(true);
    expect(isBotParticipant({ identity: 'other', name: 'ASSISTANT' })).toBe(true);
  });

  it('returns false for regular participant', () => {
    expect(isBotParticipant({ identity: 'user_alice', name: 'Alice' })).toBe(false);
  });

  it('returns false when no name and identity does not start with bot_', () => {
    expect(isBotParticipant({ identity: 'voice_assistant_user_123' })).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// mapTrack
// ---------------------------------------------------------------------------
describe('mapTrack', () => {
  it('maps all fields', () => {
    const track = mapTrack({
      sid: 'TR_abc',
      name: 'microphone',
      source: 1,
      type: 0,
      muted: false,
      mimeType: 'audio/opus',
      width: 0,
      height: 0,
    });
    expect(track.sid).toBe('TR_abc');
    expect(track.name).toBe('microphone');
    expect(track.source).toBe('1');
    expect(track.kind).toBe('0');
    expect(track.muted).toBe(false);
    expect(track.mimeType).toBe('audio/opus');
  });

  it('falls back to empty string for missing sid', () => {
    const track = mapTrack({});
    expect(track.sid).toBe('');
  });

  it('normalizeEnum returns undefined for null/undefined source', () => {
    const track = mapTrack({ sid: 'T', source: null, type: undefined });
    expect(track.source).toBeUndefined();
    expect(track.kind).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// mapParticipant
// ---------------------------------------------------------------------------
describe('mapParticipant', () => {
  it('maps basic fields', () => {
    const p = mapParticipant({
      identity: 'alice',
      sid: 'PA_1',
      name: 'Alice',
      state: 2,
      metadata: '{}',
      joinedAt: 1700000000,
      permission: { canPublish: true },
      tracks: [],
    });
    expect(p.identity).toBe('alice');
    expect(p.sid).toBe('PA_1');
    expect(p.name).toBe('Alice');
    expect(p.state).toBe('2');
    expect(p.isPublisher).toBe(true);
    expect(p.joinedAt).toMatch(/^\d{4}-/);
    expect(p.tracks).toHaveLength(0);
  });

  it('falls back to empty string identity', () => {
    const p = mapParticipant({});
    expect(p.identity).toBe('');
    expect(p.tracks).toHaveLength(0);
  });

  it('maps nested tracks', () => {
    const p = mapParticipant({
      identity: 'u',
      tracks: [{ sid: 'TR_1', muted: true }],
    });
    expect(p.tracks).toHaveLength(1);
    expect(p.tracks[0].sid).toBe('TR_1');
  });

  it('normalizes protobuf timestamp {seconds}', () => {
    const p = mapParticipant({ identity: 'u', joinedAt: { seconds: 1700000000 } });
    expect(p.joinedAt).toMatch(/^2023/);
  });
});

// ---------------------------------------------------------------------------
// mapRoom
// ---------------------------------------------------------------------------
describe('mapRoom', () => {
  it('maps all fields', () => {
    const room = mapRoom({
      name: 'my-room',
      sid: 'RM_1',
      metadata: 'meta',
      numParticipants: 3,
      activeRecording: true,
      creationTime: 1700000000,
    });
    expect(room.name).toBe('my-room');
    expect(room.sid).toBe('RM_1');
    expect(room.metadata).toBe('meta');
    expect(room.numParticipants).toBe(3);
    expect(room.activeRecording).toBe(true);
    expect(room.creationTime).toMatch(/^\d{4}-/);
  });

  it('falls back to empty string name', () => {
    expect(mapRoom({}).name).toBe('');
  });

  it('handles Date object for creationTime', () => {
    const d = new Date('2024-01-15T00:00:00.000Z');
    const room = mapRoom({ name: 'r', creationTime: d });
    expect(room.creationTime).toBe('2024-01-15T00:00:00.000Z');
  });

  it('handles string creationTime', () => {
    const room = mapRoom({ name: 'r', creationTime: '2024-01-15T00:00:00.000Z' });
    expect(room.creationTime).toBe('2024-01-15T00:00:00.000Z');
  });
});

// ---------------------------------------------------------------------------
// mapWebhookEvent
// ---------------------------------------------------------------------------
describe('mapWebhookEvent', () => {
  it('maps event, roomName, and participantIdentity', () => {
    const ev = mapWebhookEvent({
      event: 'participant_joined',
      room: { name: 'my-room' },
      participant: { identity: 'alice' },
    });
    expect(ev.source).toBe('webhook');
    expect(ev.event).toBe('participant_joined');
    expect(ev.roomName).toBe('my-room');
    expect(ev.participantIdentity).toBe('alice');
    expect(ev.receivedAt).toBeTruthy();
  });

  it('uses "unknown" for missing event field', () => {
    const ev = mapWebhookEvent({});
    expect(ev.event).toBe('unknown');
  });

  it('handles null/non-object input gracefully', () => {
    const ev = mapWebhookEvent(null);
    expect(ev.event).toBe('unknown');
    expect(ev.roomName).toBeUndefined();
  });

  it('attaches full payload', () => {
    const payload = { event: 'e', room: { name: 'r' }, foo: 'bar' };
    const ev = mapWebhookEvent(payload);
    expect((ev.payload as Record<string, unknown>)['foo']).toBe('bar');
  });
});
