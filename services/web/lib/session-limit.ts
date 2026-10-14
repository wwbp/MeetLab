/**
 * Session time limit — pure logic.
 *
 * A capped session is fully described by three values:
 *
 *   limitSeconds  the cap, from the room's bot config (0 = unlimited)
 *   anchorMs      when the first *human* joined — LiveKit's own `joinedAt`
 *   nowMs         Date.now()
 *
 * Everything the UI shows is a function of those three, so all of it lives here
 * as plain functions and the React layer is just a 1-second tick. There is no
 * stored deadline anywhere: the anchor is re-derived from the room's current
 * participants, which is what makes late joiners share one countdown for free.
 *
 * The limit is advisory. Nobody is disconnected — see docs/session-limits.md.
 */

/** Identity prefix the bot runner mints for bots (lib/concierge/bot-runner.ts). */
const BOT_IDENTITY_PREFIX = 'bot_';

/** The "you're three quarters through" warning fires at this share of the limit. */
export const WARN_AT_ELAPSED_FRACTION = 0.75;

/** Cap on the final reminder: one minute out, for any session long enough. */
const FINAL_REMINDER_CEILING_MS = 60_000;

/** …and no later than this share of the limit, so it stays clear of the warning. */
const FINAL_REMINDER_FRACTION = 0.125;

export type SessionPhase =
  | 'off' /** no limit configured, or nobody has joined yet */
  | 'running'
  | 'warn' /** three quarters elapsed */
  | 'final' /** last stretch before the deadline */
  | 'expired';

/** The subset of a LiveKit Participant this module needs. */
type TimedParticipant = {
  identity: string;
  joinedAt?: Date;
};

type SessionTimerState = {
  phase: SessionPhase;
  /** Never negative, never more than the limit. */
  remainingMs: number;
  /** 'mm:ss', or 'h:mm:ss' past an hour. Empty when the phase is 'off'. */
  clock: string;
};

/**
 * How long before the deadline the final reminder fires.
 *
 * A flat 60 seconds would collide with the three-quarter warning at exactly a
 * 4-minute limit (75% elapsed *is* one minute left), so short sessions scale the
 * reminder down instead. The result always lands strictly inside the final
 * quarter, for every limit.
 */
export function finalReminderMs(limitSeconds: number): number {
  return Math.min(FINAL_REMINDER_CEILING_MS, limitSeconds * 1000 * FINAL_REMINDER_FRACTION);
}

/**
 * When the session started: the earliest join among human participants.
 *
 * Bots are skipped — the bot is in the room before anyone else, so anchoring to
 * it would start the clock at bot startup rather than at the first human join.
 * Returns null when there is nobody to anchor to yet (LiveKit populates
 * `joinedAt` from the server, so it can briefly be undefined).
 */
export function sessionAnchorMs(participants: Iterable<TimedParticipant>): number | null {
  let anchor: number | null = null;
  for (const participant of participants) {
    if (participant.identity.startsWith(BOT_IDENTITY_PREFIX)) continue;
    const joinedAt = participant.joinedAt?.getTime();
    if (joinedAt === undefined || Number.isNaN(joinedAt)) continue;
    if (anchor === null || joinedAt < anchor) anchor = joinedAt;
  }
  return anchor;
}

export function sessionTimer(input: {
  limitSeconds: number;
  anchorMs: number | null;
  nowMs: number;
}): SessionTimerState {
  const { limitSeconds, anchorMs, nowMs } = input;
  if (limitSeconds <= 0 || anchorMs === null) {
    return { phase: 'off', remainingMs: 0, clock: '' };
  }

  const limitMs = limitSeconds * 1000;
  const elapsedMs = nowMs - anchorMs;
  // Clamped both ends: `anchorMs` is the server's clock and `nowMs` is the
  // browser's, so a skewed client must not be shown more than the full limit.
  const remainingMs = Math.min(limitMs, Math.max(0, limitMs - elapsedMs));

  const phase: SessionPhase =
    remainingMs <= 0
      ? 'expired'
      : remainingMs <= finalReminderMs(limitSeconds)
        ? 'final'
        : elapsedMs >= limitMs * WARN_AT_ELAPSED_FRACTION
          ? 'warn'
          : 'running';

  return { phase, remainingMs, clock: formatClock(remainingMs) };
}

/**
 * Countdown label. Seconds round *up* so a session with exactly five minutes
 * left reads '5:00' rather than '4:59'.
 */
export function formatClock(ms: number): string {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
  const seconds = totalSeconds % 60;
  const minutes = Math.floor(totalSeconds / 60) % 60;
  const hours = Math.floor(totalSeconds / 3600);
  const mm = hours > 0 ? String(minutes).padStart(2, '0') : String(minutes);
  const ss = String(seconds).padStart(2, '0');
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

/**
 * Which phase to treat as "already announced" after showing `phase`.
 *
 * 'off' is not recorded: it means the timer momentarily has nothing to say (no
 * anchor for a tick), and remembering it would make the next tick look like a
 * new phase and repeat a warning the participant has already seen.
 */
export function nextAnnouncedPhase(
  previousPhase: SessionPhase | null,
  phase: SessionPhase,
): SessionPhase | null {
  return phase === 'off' ? previousPhase : phase;
}

export type NoticeTone = 'info' | 'warning' | 'danger';

type SessionNotice = {
  /** The phase that triggered it — doubles as a de-duplication key. */
  phase: SessionPhase;
  message: string;
  tone: NoticeTone;
  /** Stays on screen until dismissed (only the final one does). */
  persistent: boolean;
  offerLeave: boolean;
};

/**
 * What, if anything, to tell the participant now.
 *
 * Returns a notice only on a phase *change*, so the caller can hand it every
 * tick and simply show whatever comes back. Note what it does not do: somebody
 * who joins when the session is already three quarters gone never sees the "this
 * session is limited to N minutes" intro, because by then it isn't true — they
 * get the warning for where the session actually is.
 */
export function sessionNotice(input: {
  previousPhase: SessionPhase | null;
  phase: SessionPhase;
  remainingMs: number;
  limitSeconds: number;
}): SessionNotice | null {
  const { previousPhase, phase, remainingMs, limitSeconds } = input;
  if (phase === 'off' || phase === previousPhase) return null;

  switch (phase) {
    case 'running':
      return {
        phase,
        message: `This session is limited to ${humanRemaining(limitSeconds * 1000)}`,
        tone: 'info',
        persistent: false,
        offerLeave: false,
      };
    case 'warn':
      return {
        phase,
        message: `About ${humanRemaining(remainingMs)} left in this session`,
        tone: 'warning',
        persistent: false,
        offerLeave: false,
      };
    case 'final':
      return {
        phase,
        message: `Less than ${humanRemaining(finalReminderMs(limitSeconds))} left — time to wrap up`,
        tone: 'danger',
        persistent: false,
        offerLeave: false,
      };
    case 'expired':
      return {
        phase,
        message: 'Session time is up — please wrap up and leave',
        tone: 'danger',
        persistent: true,
        offerLeave: true,
      };
  }
}

/**
 * Read the limit out of a bot-runner `GET /config` payload.
 *
 * Deliberately total: anything missing, malformed, or negative means "no limit"
 * rather than an error, because this is read on the join path and a capped
 * session is never worth failing a join over.
 */
export function sessionLimitSecondsFromConfig(payload: unknown): number {
  const minutes = (payload as { session_limit_minutes?: unknown } | null)?.session_limit_minutes;
  if (typeof minutes !== 'number' || !Number.isFinite(minutes) || minutes <= 0) return 0;
  return Math.floor(minutes) * 60;
}

/** Rounded, spoken-style duration for notification copy. */
export function humanRemaining(ms: number): string {
  if (ms < 60_000) {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    return `${seconds} second${seconds === 1 ? '' : 's'}`;
  }
  const minutes = Math.round(ms / 60_000);
  return `${minutes} minute${minutes === 1 ? '' : 's'}`;
}
