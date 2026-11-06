/**
 * What a participant sees on a group start link between Join and the room. Pure so it can
 * be tested; app/start/[token]/StartClient.tsx renders it and polls for the next view.
 */
import type { TicketView } from './matchmaking-client';
import { carryQuery } from './join';
import { formatClock } from './session-limit';

const PAGE_TITLE = 'Join meeting | MeetLab';

export const COPY = {
  startHeading: 'Join the group meeting',
  startMessage:
    'You will wait for the others, then join a call with them and an assistant. Keep this page open and in front.',
  passwordLabel: 'Meeting password',
  passwordHelp: 'It is on the survey page.',
  wrongPassword: 'That password is not right. Check the survey page and try again.',
  locked: 'Too many tries. Wait 1 minute, then try again.',
  closedHeading: 'This link is closed',
  closedMessage:
    'The study is not taking new participants now. Go back to the survey and contact the researcher.',
  connectionLost: 'Connection lost. Trying again…',
};

export type Waiting = {
  heading: string;
  message: string;
  note?: string;
  /** Digits only, shown aria-hidden; `announce` is what a screen reader hears, once. */
  countdown?: string;
  announce?: string;
  action?: 'ready' | 'join_again';
};

/** Time left on a reply's countdown, measured from when the reply arrived (not the browser clock). */
export const remainingMs = (inMs: number, arrivedAtMs: number, nowMs: number) =>
  Math.max(0, inMs - (nowMs - arrivedAtMs));

export function waitingView(view: TicketView, arrivedAtMs: number, nowMs: number): Waiting {
  switch (view.kind) {
    case 'searching': {
      const by = new Date(arrivedAtMs + view.expires_in_ms).toLocaleTimeString([], {
        hour: 'numeric',
        minute: '2-digit',
      });
      return {
        heading: `Finding your group: ${view.found} of ${view.size} here`,
        message: `Keep this page open. We will look for a group until ${by}.`,
        note: view.requeued
          ? 'Someone did not confirm in time. You are back in line with your place kept.'
          : undefined,
      };
    }
    case 'ready_check':
      return view.pressed
        ? { heading: 'Your group is ready', message: 'Waiting for the others to confirm.' }
        : {
            heading: 'Your group is ready',
            message: `Check your Prolific ID, then press the button within ${Math.ceil(remainingMs(view.deadline_in_ms, arrivedAtMs, nowMs) / 1000)} seconds.`,
            action: 'ready',
          };
    case 'starting': {
      const message = 'Next: allow your microphone and type your name.';
      if (!view.room) return { heading: 'Setting up the room…', message };
      return {
        heading: 'Starting in',
        message,
        countdown: formatClock(remainingMs(view.starts_in_ms, arrivedAtMs, nowMs)),
        announce: `Starting in ${Math.ceil(view.starts_in_ms / 1000)} seconds`,
      };
    }
    case 'expired':
      return {
        heading: 'We could not form a group in time',
        message: 'Go back to the survey and follow its instructions. You can close this page.',
      };
    case 'missed':
      return {
        heading: 'You missed the start',
        message:
          "Your group was ready, but we didn't hear from you in time. You can join the line again.",
        action: 'join_again',
      };
    case 'done':
      return { heading: 'Your meeting has ended', message: 'You can close this page.' };
  }
}

/** The room to go to: the start page's query, with the ticket's Prolific ID winning (R34). */
export function roomUrl(room: string, participantId: string | null, search: string): string {
  const url = carryQuery(`/rooms/${encodeURIComponent(room)}`, search);
  return participantId ? carryQuery(url, `?PROLIFIC_PID=${participantId}`) : url;
}

/** What to do on a change of view kind: the tab title, a tone, where focus goes (R39). */
export function attention(
  prev: TicketView['kind'] | null,
  next: TicketView['kind'],
): { title: string; tone: boolean; focus: 'ready' | null } | null {
  if (prev === next) return null;
  if (next === 'ready_check') {
    return { title: '(!) Your group is ready - MeetLab', tone: true, focus: 'ready' };
  }
  return { title: PAGE_TITLE, tone: false, focus: null };
}
