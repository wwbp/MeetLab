import { describe, expect, it } from 'vitest';
import { attention, roomUrl, waitingView } from './waiting';

const T0 = Date.UTC(2026, 10, 2, 20, 0, 0);
const clock = (ms: number) =>
  new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

describe('waitingView', () => {
  it('searching: how many are here, and the time a code comes if no group forms', () => {
    const v = waitingView(
      { kind: 'searching', found: 2, size: 3, expires_in_ms: 225_000, requeued: false },
      T0,
      T0 + 5_000,
    );
    expect(v.heading).toBe('Finding your group: 2 of 3 here');
    expect(v.message).toBe(
      `Keep this page open. If no group forms by ${clock(T0 + 225_000)}, you will get a code so you can still be paid.`,
    );
    expect(v.note).toBeUndefined();
    expect(v.action).toBeUndefined();
  });

  it('searching after a failed ready check says the place was kept', () => {
    const v = waitingView(
      { kind: 'searching', found: 1, size: 3, expires_in_ms: 60_000, requeued: true },
      T0,
      T0,
    );
    expect(v.note).toBe('Someone did not confirm in time. You are back in line with your place kept.');
  });

  it('ready check: a Ready button, then waiting for the others', () => {
    const v = waitingView({ kind: 'ready_check', deadline_in_ms: 20_000, pressed: false }, T0, T0);
    expect(v).toMatchObject({
      heading: 'Your group is ready',
      message: 'Press the button within 20 seconds.',
      action: 'ready',
    });
    const pressed = waitingView({ kind: 'ready_check', deadline_in_ms: 9_000, pressed: true }, T0, T0);
    expect(pressed.message).toBe('Waiting for the others to confirm.');
    expect(pressed.action).toBeUndefined();
  });

  it('starting: counts down from when the reply arrived, whatever the browser clock says', () => {
    const view = { kind: 'starting', room: 'link-a', starts_in_ms: 8_000 } as const;
    // The browser clock is an hour off; only the time since arrival counts.
    const skewed = T0 + 3_600_000;
    const v = waitingView(view, skewed, skewed + 3_000);
    expect(v.heading).toBe('Starting in');
    expect(v.countdown).toBe('0:05');
    expect(v.announce).toBe('Starting in 8 seconds');
    expect(v.message).toBe('Next: allow your microphone and type your name.');
    expect(waitingView(view, skewed, skewed + 9_000).countdown).toBe('0:00');
  });

  it('starting without a room yet: setting up', () => {
    const v = waitingView({ kind: 'starting', room: null, starts_in_ms: 0 }, T0, T0);
    expect(v.heading).toBe('Setting up the room…');
    expect(v.countdown).toBeUndefined();
  });

  it('expired: the wait code to copy', () => {
    const v = waitingView({ kind: 'expired', code: 'ABCD2345' }, T0, T0);
    expect(v).toMatchObject({
      heading: 'We could not form a group in time',
      code: 'ABCD2345',
      message: 'Paste it into the survey to be paid. You can close this page.',
      action: 'copy',
    });
  });

  it('missed: join the line again', () => {
    const v = waitingView({ kind: 'missed' }, T0, T0);
    expect(v).toMatchObject({
      heading: 'You missed the start',
      message: "Your group was ready, but we didn't hear from you in time. You can join the line again.",
      action: 'join_again',
    });
  });

  it('done: the meeting is over', () => {
    expect(waitingView({ kind: 'done' }, T0, T0).heading).toBe('Your meeting has ended');
  });
});

describe('roomUrl', () => {
  it("carries the start page's query but the ticket's Prolific ID wins", () => {
    expect(
      roomUrl('link-a', '5f8e2a1b3c4d5e6f7a8b9c0d', '?PROLIFIC_PID=typo&STUDY_ID=s1'),
    ).toBe('/rooms/link-a?PROLIFIC_PID=5f8e2a1b3c4d5e6f7a8b9c0d&STUDY_ID=s1');
  });

  it('size 1 (no ticket ID) keeps the query as it was', () => {
    expect(roomUrl('link-a', null, '?PROLIFIC_PID=abc')).toBe('/rooms/link-a?PROLIFIC_PID=abc');
  });
});

describe('attention', () => {
  it('entering the ready check: title, tone, focus on Ready', () => {
    expect(attention('searching', 'ready_check')).toEqual({
      title: '(!) Your group is ready - MeetLab',
      tone: true,
      focus: 'ready',
    });
  });

  it('expired moves focus to Copy; other changes restore the title', () => {
    expect(attention('searching', 'expired')).toEqual({
      title: 'Join meeting | MeetLab',
      tone: false,
      focus: 'copy',
    });
    expect(attention('ready_check', 'starting').title).toBe('Join meeting | MeetLab');
  });

  it('nothing when the state did not change', () => {
    expect(attention('ready_check', 'ready_check')).toBeNull();
  });
});
