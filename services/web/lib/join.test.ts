import { describe, expect, it } from 'vitest';
import { DisconnectReason } from 'livekit-client';
import {
  JOIN_FAILED,
  PRE_JOIN_DEFAULTS,
  carryQuery,
  endsStudy,
  mediaErrorMessage,
  readConnectionDetails,
} from './join';

describe('carryQuery', () => {
  it('carries the Prolific ID onto the room URL', () => {
    expect(carryQuery('/rooms/abc', '?PROLIFIC_PID=0123456789abcdef01234567')).toBe(
      '/rooms/abc?PROLIFIC_PID=0123456789abcdef01234567',
    );
  });

  it('merges with params the room URL already has, without duplicating', () => {
    expect(carryQuery('/rooms/abc?hq=true&PROLIFIC_PID=x', '?PROLIFIC_PID=x&STUDY_ID=s')).toBe(
      '/rooms/abc?hq=true&PROLIFIC_PID=x&STUDY_ID=s',
    );
  });

  it('keeps an absolute URL absolute', () => {
    expect(carryQuery('https://meet.example.org/rooms/abc', '?PROLIFIC_PID=p')).toBe(
      'https://meet.example.org/rooms/abc?PROLIFIC_PID=p',
    );
  });

  it('leaves the URL alone when there is nothing to carry', () => {
    expect(carryQuery('/rooms/abc', '')).toBe('/rooms/abc');
  });
});

describe('readConnectionDetails', () => {
  it('returns the details from a successful response', async () => {
    const resp = new Response(JSON.stringify({ roomName: 'r' }), { status: 200 });
    expect(await readConnectionDetails(resp)).toEqual({ roomName: 'r' });
  });

  it('throws the plain join message on a plain-text error response', async () => {
    const resp = new Response('Too many requests', { status: 429 });
    await expect(readConnectionDetails(resp)).rejects.toThrow(JOIN_FAILED);
  });
});

describe('endsStudy', () => {
  it('ends the study when the participant left', () => {
    expect(endsStudy(DisconnectReason.CLIENT_INITIATED)).toBe(true);
  });

  it('ends the study when the meeting was closed', () => {
    expect(endsStudy(DisconnectReason.ROOM_DELETED)).toBe(true);
  });

  it('does not end the study on a dropped connection', () => {
    expect(endsStudy(DisconnectReason.SIGNAL_CLOSE)).toBe(false);
    expect(endsStudy(DisconnectReason.UNKNOWN_REASON)).toBe(false);
    expect(endsStudy(undefined)).toBe(false);
  });
});

describe('mediaErrorMessage', () => {
  it('tells them how to allow the microphone when permission is denied', () => {
    const denied = new DOMException('Permission denied', 'NotAllowedError');
    expect(mediaErrorMessage(denied)).toMatch(/allow the microphone.*address bar.*reload/i);
    expect(mediaErrorMessage(new Error('Permission denied'))).toMatch(/allow the microphone/i);
  });

  it('gives plain copy for anything else, never pointing at console logs', () => {
    const msg = mediaErrorMessage(new Error('NotReadableError: device in use'));
    expect(msg).not.toMatch(/console/i);
    expect(msg).not.toMatch(/allow the microphone/i);
    expect(msg.length).toBeGreaterThan(0);
  });
});

describe('PRE_JOIN_DEFAULTS', () => {
  it('starts with the camera off and the microphone on (a voice study)', () => {
    expect(PRE_JOIN_DEFAULTS.videoEnabled).toBe(false);
    expect(PRE_JOIN_DEFAULTS.audioEnabled).toBe(true);
  });
});
