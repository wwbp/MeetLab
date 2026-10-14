import { describe, it, expect } from 'vitest';
import {
  finalReminderMs,
  formatClock,
  humanRemaining,
  nextAnnouncedPhase,
  sessionAnchorMs,
  sessionLimitSecondsFromConfig,
  sessionNotice,
  sessionTimer,
  WARN_AT_ELAPSED_FRACTION,
  type SessionPhase,
} from './session-limit';

const MIN = 60_000;

/** Shorthand for the participant shape sessionAnchorMs consumes. */
const p = (identity: string, joinedAtMs?: number) => ({
  identity,
  joinedAt: joinedAtMs === undefined ? undefined : new Date(joinedAtMs),
});

describe('sessionAnchorMs', () => {
  it('returns the earliest human join time', () => {
    expect(sessionAnchorMs([p('bob__a1', 5_000), p('ann__b2', 3_000)])).toBe(3_000);
  });

  it('ignores the bot, whose identity is prefixed bot_', () => {
    // The bot joins before any human; anchoring to it would start the clock at
    // bot init, which is exactly what this feature must not do.
    expect(sessionAnchorMs([p('bot_link-abc_9f2', 1_000), p('ann__b2', 4_000)])).toBe(4_000);
  });

  it('returns null when only the bot is present', () => {
    expect(sessionAnchorMs([p('bot_link-abc_9f2', 1_000)])).toBeNull();
  });

  it('returns null for an empty room', () => {
    expect(sessionAnchorMs([])).toBeNull();
  });

  it('skips participants whose joinedAt has not arrived yet', () => {
    expect(sessionAnchorMs([p('ann__b2'), p('bob__a1', 7_000)])).toBe(7_000);
  });

  it('returns null when no participant has a joinedAt yet', () => {
    expect(sessionAnchorMs([p('ann__b2'), p('bob__a1')])).toBeNull();
  });
});

describe('finalReminderMs', () => {
  it('is one minute for long sessions', () => {
    expect(finalReminderMs(60 * 60)).toBe(MIN);
    expect(finalReminderMs(10 * 60)).toBe(MIN);
  });

  it('shrinks for short sessions so it never collides with the 3/4 warning', () => {
    // A flat 60s reminder would fire at the exact instant of the 75% mark on a
    // 4-minute limit. 12.5% of the limit always leaves half the final quarter.
    expect(finalReminderMs(4 * 60)).toBe(30_000);
    expect(finalReminderMs(2 * 60)).toBe(15_000);
  });

  it('always lands strictly inside the final quarter', () => {
    for (const minutes of [1, 2, 4, 5, 8, 10, 20, 40, 60, 90]) {
      const limitMs = minutes * MIN;
      const warnRemaining = limitMs * (1 - WARN_AT_ELAPSED_FRACTION);
      expect(finalReminderMs(minutes * 60)).toBeLessThan(warnRemaining);
      expect(finalReminderMs(minutes * 60)).toBeGreaterThan(0);
    }
  });
});

describe('sessionTimer', () => {
  const at = (elapsedMs: number, limitSeconds = 60 * 60) =>
    sessionTimer({ limitSeconds, anchorMs: 1_000_000, nowMs: 1_000_000 + elapsedMs });

  it('is off when no limit is configured', () => {
    const state = sessionTimer({ limitSeconds: 0, anchorMs: 1_000_000, nowMs: 2_000_000 });
    expect(state.phase).toBe('off');
    expect(state.remainingMs).toBe(0);
    expect(state.clock).toBe('');
  });

  it('is off for a nonsensical negative limit rather than instantly expired', () => {
    expect(sessionTimer({ limitSeconds: -5, anchorMs: 1_000, nowMs: 2_000 }).phase).toBe('off');
  });

  it('is off before anyone has joined', () => {
    expect(sessionTimer({ limitSeconds: 600, anchorMs: null, nowMs: 1_000 }).phase).toBe('off');
  });

  it('counts down from the anchor, not from now', () => {
    const state = at(10 * MIN);
    expect(state.phase).toBe('running');
    expect(state.remainingMs).toBe(50 * MIN);
    expect(state.clock).toBe('50:00');
  });

  it('enters warn exactly at the 3/4 mark', () => {
    expect(at(45 * MIN - 1).phase).toBe('running');
    expect(at(45 * MIN).phase).toBe('warn');
  });

  it('enters final exactly at the final-reminder threshold', () => {
    expect(at(59 * MIN - 1).phase).toBe('warn');
    expect(at(59 * MIN).phase).toBe('final');
  });

  it('goes straight from warn to final on a short limit without skipping either', () => {
    const phases = [1, 2, 2.5, 3, 3.5, 3.9].map((m) => at(m * MIN, 4 * 60).phase);
    expect(phases).toEqual(['running', 'running', 'running', 'warn', 'final', 'final']);
  });

  it('expires at zero and never reports negative time', () => {
    const state = at(75 * MIN);
    expect(state.phase).toBe('expired');
    expect(state.remainingMs).toBe(0);
    expect(state.clock).toBe('0:00');
  });

  it('expires exactly on the deadline', () => {
    expect(at(60 * MIN - 1).phase).toBe('final');
    expect(at(60 * MIN).phase).toBe('expired');
  });

  it('clamps to the limit if the client clock is behind the join time', () => {
    // joinedAt comes from the server; a slow client clock must not show more
    // time than the limit allows.
    const state = sessionTimer({ limitSeconds: 600, anchorMs: 5_000, nowMs: 1_000 });
    expect(state.remainingMs).toBe(10 * MIN);
    expect(state.phase).toBe('running');
  });
});

describe('sessionNotice', () => {
  const notice = (
    previousPhase: SessionPhase | null,
    phase: SessionPhase,
    remainingMs = 10 * MIN,
    limitSeconds = 30 * 60,
  ) => sessionNotice({ previousPhase, phase, remainingMs, limitSeconds });

  it('announces the limit once, when the countdown first appears', () => {
    const first = notice(null, 'running', 30 * MIN);
    expect(first?.tone).toBe('info');
    expect(first?.message).toBe('This session is limited to 30 minutes');
  });

  it('says nothing while the phase is unchanged', () => {
    expect(notice('running', 'running')).toBeNull();
    expect(notice('warn', 'warn')).toBeNull();
    expect(notice('expired', 'expired')).toBeNull();
  });

  it('says nothing when there is no limit', () => {
    expect(notice(null, 'off')).toBeNull();
    expect(notice('running', 'off')).toBeNull();
  });

  it('warns at the three-quarter mark with the time actually left', () => {
    const warn = notice('running', 'warn', 7.5 * MIN);
    expect(warn?.tone).toBe('warning');
    expect(warn?.message).toBe('About 8 minutes left in this session');
    expect(warn?.persistent).toBe(false);
  });

  it('gives a final reminder scaled to the limit', () => {
    expect(notice('warn', 'final', 60_000)?.message).toBe(
      'Less than 1 minute left — time to wrap up',
    );
    expect(notice('warn', 'final', 30_000, 4 * 60)?.message).toBe(
      'Less than 30 seconds left — time to wrap up',
    );
  });

  it('stays on screen at zero and offers a way out', () => {
    const done = notice('final', 'expired', 0);
    expect(done?.tone).toBe('danger');
    expect(done?.persistent).toBe(true);
    expect(done?.offerLeave).toBe(true);
    expect(done?.message).toBe('Session time is up — please wrap up and leave');
  });

  it('skips the intro for someone who joins mid-session', () => {
    // Their pill already shows the real time left, so leading with "limited to
    // 30 minutes" would be a lie. They get the current state instead.
    expect(notice(null, 'warn', 7 * MIN)?.tone).toBe('warning');
    expect(notice(null, 'final', 30_000)?.tone).toBe('danger');
    expect(notice(null, 'expired', 0)?.persistent).toBe(true);
  });
});

describe('nextAnnouncedPhase', () => {
  it('remembers the phase that was announced', () => {
    expect(nextAnnouncedPhase(null, 'running')).toBe('running');
    expect(nextAnnouncedPhase('warn', 'final')).toBe('final');
  });

  it('holds onto the last real phase through an off gap', () => {
    // 'off' means "we momentarily cannot tell" — e.g. the anchor is unavailable
    // for a tick because everyone else's participant info is in flux. Recording
    // it would make the next tick look like a fresh phase and re-fire a warning
    // the participant has already seen.
    expect(nextAnnouncedPhase('warn', 'off')).toBe('warn');
    expect(nextAnnouncedPhase(null, 'off')).toBeNull();
  });

  it('does not repeat a warning after a transient gap', () => {
    const phases: SessionPhase[] = ['running', 'running', 'warn', 'off', 'warn', 'warn', 'final'];
    const announced: string[] = [];
    let previousPhase: SessionPhase | null = null;

    for (const phase of phases) {
      const notice = sessionNotice({
        previousPhase,
        phase,
        remainingMs: 60_000,
        limitSeconds: 30 * 60,
      });
      if (notice) announced.push(notice.phase);
      previousPhase = nextAnnouncedPhase(previousPhase, phase);
    }

    expect(announced).toEqual(['running', 'warn', 'final']);
  });
});

describe('a whole session, second by second', () => {
  const SEVERITY: Record<SessionPhase, number> = {
    off: 0,
    running: 1,
    warn: 2,
    final: 3,
    expired: 4,
  };

  // Walks real time forward through a session and checks the properties that
  // matter to a participant watching the pill: it only ever counts down, the
  // phase only ever escalates, every phase is reached, and each notification
  // fires exactly once.
  for (const minutes of [2, 4, 10, 30, 60]) {
    it(`holds its invariants across a ${minutes}-minute limit`, () => {
      const limitSeconds = minutes * 60;
      const anchorMs = 1_700_000_000_000;
      const seen: SessionPhase[] = [];
      const messages: string[] = [];
      let previousPhase: SessionPhase | null = null;
      let previousRemaining = Infinity;

      for (let second = 0; second <= limitSeconds + 30; second++) {
        const { phase, remainingMs } = sessionTimer({
          limitSeconds,
          anchorMs,
          nowMs: anchorMs + second * 1000,
        });

        expect(remainingMs).toBeLessThanOrEqual(previousRemaining);
        expect(remainingMs).toBeGreaterThanOrEqual(0);
        expect(remainingMs).toBeLessThanOrEqual(limitSeconds * 1000);
        previousRemaining = remainingMs;

        if (previousPhase !== null) {
          expect(SEVERITY[phase]).toBeGreaterThanOrEqual(SEVERITY[previousPhase]);
        }

        const notice = sessionNotice({ previousPhase, phase, remainingMs, limitSeconds });
        if (phase !== previousPhase) {
          seen.push(phase);
          expect(notice).not.toBeNull();
          messages.push(notice!.message);
        } else {
          expect(notice).toBeNull();
        }
        previousPhase = phase;
      }

      expect(seen).toEqual(['running', 'warn', 'final', 'expired']);
      expect(new Set(messages).size).toBe(4);
    });
  }

  it('never starts the clock while only the bot is in the room', () => {
    const anchorMs = sessionAnchorMs([p('bot_link-abc_9f2', 1_000)]);
    for (const second of [0, 60, 3_600]) {
      expect(sessionTimer({ limitSeconds: 600, anchorMs, nowMs: second * 1000 }).phase).toBe('off');
    }
  });

  it('re-anchors to whoever is left, which is what resets an emptied room', () => {
    // Documented consequence of deriving the anchor from live participants:
    // when the first joiner leaves, the countdown restarts from the next
    // earliest join. See docs/session-limits.md.
    const early = p('ann__b2', 1_000);
    const late = p('bob__a1', 60_000);
    expect(sessionAnchorMs([early, late])).toBe(1_000);
    expect(sessionAnchorMs([late])).toBe(60_000);
  });
});

describe('formatClock', () => {
  it('rounds up so a full minute reads as that minute', () => {
    expect(formatClock(5 * MIN)).toBe('5:00');
    expect(formatClock(5 * MIN - 1)).toBe('5:00');
  });

  it('pads seconds but not minutes', () => {
    expect(formatClock(9_000)).toBe('0:09');
    expect(formatClock(69_000)).toBe('1:09');
  });

  it('shows hours only when needed', () => {
    expect(formatClock(59 * MIN)).toBe('59:00');
    expect(formatClock(60 * MIN)).toBe('1:00:00');
    expect(formatClock(95 * MIN + 3_000)).toBe('1:35:03');
  });

  it('floors at zero', () => {
    expect(formatClock(0)).toBe('0:00');
    expect(formatClock(-5_000)).toBe('0:00');
  });
});

describe('sessionLimitSecondsFromConfig', () => {
  it('converts the runner config minutes to seconds', () => {
    expect(sessionLimitSecondsFromConfig({ session_limit_minutes: 45 })).toBe(45 * 60);
  });

  it('treats 0 as unlimited', () => {
    expect(sessionLimitSecondsFromConfig({ session_limit_minutes: 0 })).toBe(0);
  });

  it('falls back to unlimited for anything unusable', () => {
    // A runner that is down, on an older build, or returning junk must never
    // impose a limit — joining a room cannot depend on this field existing.
    for (const payload of [
      {},
      null,
      undefined,
      'nope',
      { session_limit_minutes: null },
      { session_limit_minutes: 'ten' },
      { session_limit_minutes: -5 },
      { session_limit_minutes: Number.NaN },
    ]) {
      expect(sessionLimitSecondsFromConfig(payload)).toBe(0);
    }
  });

  it('floors fractional minutes', () => {
    expect(sessionLimitSecondsFromConfig({ session_limit_minutes: 2.9 })).toBe(2 * 60);
  });
});

describe('humanRemaining', () => {
  it('rounds to whole minutes for toast copy', () => {
    expect(humanRemaining(15 * MIN)).toBe('15 minutes');
    expect(humanRemaining(14 * MIN + 40_000)).toBe('15 minutes');
    expect(humanRemaining(1 * MIN)).toBe('1 minute');
  });

  it('describes sub-minute time in seconds', () => {
    expect(humanRemaining(30_000)).toBe('30 seconds');
    expect(humanRemaining(0)).toBe('0 seconds');
  });

  it('never rounds a sub-minute value up to a bare minute', () => {
    // 'about 60 seconds' reads oddly, and '1 minute' would overstate it.
    expect(humanRemaining(59_999)).toBe('59 seconds');
    expect(humanRemaining(1_000)).toBe('1 second');
  });
});
