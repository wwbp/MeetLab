'use client';

import { Room } from 'livekit-client';
import * as React from 'react';
import { toast } from 'sonner';
import {
  nextAnnouncedPhase,
  sessionAnchorMs,
  sessionNotice,
  sessionTimer,
  type NoticeTone,
  type SessionPhase,
} from './session-limit';

const TOAST_ID = 'session-limit';

const TONE_STYLE: Record<NoticeTone, React.CSSProperties> = {
  info: {},
  warning: { color: '#fbbf24' },
  danger: { backgroundColor: 'var(--lk-danger3)', color: 'var(--lk-fg)' },
};

const PILL_COLOR: Record<SessionPhase, string> = {
  off: 'var(--lk-fg)',
  running: 'var(--lk-fg)',
  warn: '#fbbf24',
  final: 'var(--lk-danger)',
  expired: 'var(--lk-danger)',
};

/**
 * Countdown for a time-capped session, plus the notifications leading up to the
 * cap. Every decision — how much time is left, which phase that is, what to say
 * about it — comes from ./session-limit, which is pure and unit-tested; this is
 * the 1-second tick and the markup around it.
 *
 * The countdown starts when the first human joined (LiveKit's own `joinedAt`), so
 * everyone in the room shares one clock and a late joiner sees the time that is
 * actually left. Advisory: at zero the timer says so and offers to leave, but
 * nobody is disconnected.
 */
export function SessionTimer({ room, limitSeconds }: { room: Room; limitSeconds: number }) {
  const [nowMs, setNowMs] = React.useState(() => Date.now());

  React.useEffect(() => {
    if (limitSeconds <= 0) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, [limitSeconds]);

  // Re-derived every tick, so participants joining or leaving needs no wiring.
  const anchorMs = sessionAnchorMs([room.localParticipant, ...room.remoteParticipants.values()]);
  const { phase, remainingMs, clock } = sessionTimer({ limitSeconds, anchorMs, nowMs });

  const previousPhase = React.useRef<SessionPhase | null>(null);
  React.useEffect(() => {
    const notice = sessionNotice({
      previousPhase: previousPhase.current,
      phase,
      remainingMs,
      limitSeconds,
    });
    previousPhase.current = nextAnnouncedPhase(previousPhase.current, phase);
    if (!notice) return;

    toast(notice.message, {
      id: TOAST_ID,
      position: 'top-center',
      className: 'lk-button',
      icon: notice.tone === 'danger' ? '⏰' : '⏳',
      duration: notice.persistent ? Infinity : 8_000,
      style: TONE_STYLE[notice.tone],
      action: notice.offerLeave ? { label: 'Leave', onClick: () => room.disconnect() } : undefined,
    });
    // remainingMs is deliberately absent from the deps: it changes every second,
    // and the notice only depends on it at the instant the phase flips.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, limitSeconds, room]);

  if (phase === 'off') return null;

  return (
    <div
      role="timer"
      // Silent for screen readers: a per-second update would be unusable noise.
      // The milestone toasts carry the announcements instead.
      aria-live="off"
      title={
        phase === 'expired'
          ? 'This session has reached its time limit'
          : `${clock} left in this session`
      }
      style={{
        position: 'absolute',
        top: 8,
        left: 8,
        zIndex: 10,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        background: 'rgba(0,0,0,0.55)',
        color: PILL_COLOR[phase],
        fontSize: '0.75rem',
        fontVariantNumeric: 'tabular-nums',
        padding: '4px 10px',
        borderRadius: 4,
        pointerEvents: 'none',
        whiteSpace: 'nowrap',
      }}
    >
      <span aria-hidden="true">{phase === 'expired' ? '⏰' : '⏳'}</span>
      <span>{phase === 'expired' ? "0:00 · time's up" : clock}</span>
    </div>
  );
}
