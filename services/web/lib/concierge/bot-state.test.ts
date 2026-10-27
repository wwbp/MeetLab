import { describe, it, expect } from 'vitest';
import { botState } from './bot-state';

// The Rooms page's Bot column: plain words, how long a bot has been joining, and a hint
// when that is slow (no machine was ready: docs/study-support.md, "Prepare for study").
describe('botState', () => {
  const now = Date.parse('2026-10-27T14:10:00Z');
  const ago = (s: number) => new Date(now - s * 1000).toISOString();

  it('says each known status in plain words', () => {
    expect(botState('missing', undefined, now)).toEqual({ label: 'No bot' });
    expect(botState('connected', undefined, now)).toEqual({ label: 'In the room' });
    expect(botState('connected_no_tracks', undefined, now)).toEqual({ label: 'In the room, no audio yet' });
  });

  it('counts how long a bot has been joining', () => {
    expect(botState('starting', ago(130), now)).toEqual({ label: 'Joining… 2m 10s' });
    expect(botState('starting', ago(5), now)).toEqual({ label: 'Joining… 5s' });
  });

  it('adds a hint after three minutes', () => {
    expect(botState('starting', ago(179), now).hint).toBeUndefined();
    expect(botState('starting', ago(185), now)).toEqual({
      label: 'Joining… 3m 5s',
      hint: 'Slow? Use Prepare for study before sessions',
    });
  });

  it('says just "Joining…" without a start time', () => {
    expect(botState('starting', undefined, now)).toEqual({ label: 'Joining…' });
  });

  it('shows an unknown status as it is, never a blank cell', () => {
    expect(botState('rebooting', undefined, now)).toEqual({ label: 'rebooting' });
  });
});
