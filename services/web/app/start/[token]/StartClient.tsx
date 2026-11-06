'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { buttonClass, inputClass } from '@/components/console/swiss';
import type { TicketView } from '@/lib/matchmaking-client';
import { normalizeProlificId, prolificIdFromParams } from '@/lib/study';
import { COPY, type Waiting, attention, remainingMs, roomUrl, waitingView } from '@/lib/waiting';

const POLL_MS = 2_000;
const BAD_ID = 'A Prolific ID is 24 letters and numbers: please check it.';
const ID_IN_USE = 'Someone in this line already has that Prolific ID: please check it.';

type Reply = { ticket: string; participant_id: string | null; view: TicketView; at: number };
type Action = NonNullable<Waiting['action']>;

/** A ticket this tab already holds resumes (reload), even once the link is stopped. */
export function firstScreen(stored: string | null, active: boolean) {
  return stored ? 'resume' : active ? 'form' : 'closed';
}

/** Size 1 keeps no ID on its ticket, so the page carries what was typed to the room. */
export function soloRoomUrl(room: string, typed: string, search: string) {
  return roomUrl(room, normalizeProlificId(typed), search);
}

/** Short tone on entering the ready check; the context was made in the Join click (R39). */
function beep(ctx: AudioContext) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.frequency.value = 880;
  gain.gain.value = 0.1;
  osc.connect(gain).connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + 0.3);
}

async function post(path: string, body: object) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return { res, data: await res.json().catch(() => ({})) };
}

const Page = ({ children }: { children: React.ReactNode }) => (
  <div className="flex min-h-screen items-center px-6 sm:px-16">
    <div className="w-full max-w-md space-y-8">{children}</div>
  </div>
);

type IdField = { value: string; error?: string; onChange: (value: string) => void };

function ProlificIdField({ value, error, onChange }: IdField) {
  return (
    <div className="space-y-1.5">
      <label htmlFor="prolific-id" className="text-sm font-medium">
        Prolific ID
      </label>
      <input
        id="prolific-id"
        className={inputClass}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="24-character Prolific ID"
        autoComplete="off"
        spellCheck={false}
        aria-describedby={error ? 'prolific-id-error' : undefined}
      />
      {error && (
        <p id="prolific-id-error" role="alert" className="text-signal text-sm">
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * One state of the wait. The polite region (heading + message) changes only when the
 * state does; the countdown's digits are hidden from screen readers, which hear
 * `announce` once (R39).
 */
export function WaitingPanel({
  w,
  lost,
  onAction,
  actionRef,
  id,
}: {
  w: Waiting;
  lost: boolean;
  onAction: (action: Action) => void;
  actionRef?: React.Ref<HTMLButtonElement>;
  /** The Prolific ID, checked before "I'm ready". */
  id?: IdField;
}) {
  const label = { ready: "I'm ready", join_again: 'Join again' };
  return (
    <>
      <div aria-live="polite" className="space-y-3">
        <h1 className="text-5xl font-bold tracking-tight">{w.heading}</h1>
        <p
          role={w.action === 'ready' ? 'alert' : undefined}
          className="text-muted-foreground text-base"
        >
          {w.message}
        </p>
        {w.note && <p className="text-base">{w.note}</p>}
        {w.announce && <span className="sr-only">{w.announce}</span>}
      </div>
      {w.countdown && (
        <span aria-hidden="true" className="block text-6xl font-bold tabular-nums">
          {w.countdown}
        </span>
      )}
      {w.action === 'ready' && id && <ProlificIdField {...id} />}
      {w.action && (
        <button ref={actionRef} onClick={() => onAction(w.action!)} className={buttonClass}>
          {label[w.action]}
        </button>
      )}
      {lost && (
        <p role="alert" className="text-signal text-sm">
          {COPY.connectionLost}
        </p>
      )}
    </>
  );
}

export function StartClient({
  token,
  linkId,
  size,
  needsPassword,
  active,
}: {
  token: string;
  linkId: string;
  size: number;
  needsPassword: boolean;
  active: boolean;
}) {
  const key = `ticket:${linkId}`;
  const group = size > 1;
  const [screen, setScreen] = useState<'form' | 'closed' | 'ticket'>(active ? 'form' : 'closed');
  const [reply, setReply] = useState<Reply | null>(null);
  const [now, setNow] = useState(0);
  // Prefilled from the study link's ?PROLIFIC_PID=, still editable (as on pre-join).
  const [pid, setPid] = useState(() =>
    typeof window === 'undefined'
      ? ''
      : prolificIdFromParams(new URLSearchParams(window.location.search)),
  );
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [idError, setIdError] = useState('');
  const [failures, setFailures] = useState(0);
  const audio = useRef<AudioContext | null>(null);
  const actionRef = useRef<HTMLButtonElement>(null);
  const prevKind = useRef<TicketView['kind'] | null>(null);

  const apply = useCallback((data: Omit<Reply, 'at'>) => {
    const at = Date.now();
    setReply({ ...data, at });
    if (data.participant_id) setPid((typed) => typed || (data.participant_id as string));
    setNow(at);
    setScreen('ticket');
  }, []);

  const poll = useCallback(
    async (ticket: string) => {
      try {
        const { res, data } = await post('/api/start-link/status', { ticket });
        if (res.ok) {
          setFailures(0);
          apply(data);
          return;
        }
        if (res.status === 404 || res.status === 400) {
          sessionStorage.removeItem(key);
          setReply(null);
          setScreen(active ? 'form' : 'closed');
          return;
        }
      } catch {
        // counted below
      }
      setFailures((f) => f + 1);
    },
    [apply, key, active],
  );

  // A ticket this tab holds resumes (reload).
  useEffect(() => {
    const stored = sessionStorage.getItem(key);
    // ponytail: the form shows for one poll's round trip before the ticket's view.
    if (stored && firstScreen(stored, active) === 'resume') void poll(stored);
  }, [key, active, poll]);

  const view = reply?.view;
  const ticket = reply?.ticket;
  const waiting =
    view?.kind === 'searching' ||
    view?.kind === 'ready_check' ||
    (view?.kind === 'starting' && !view.room);

  // Poll every 2 s while waiting, and at once when the tab comes back to the front.
  useEffect(() => {
    if (!ticket || !waiting) return;
    const id = setInterval(() => void poll(ticket), POLL_MS);
    const onVisible = () => document.visibilityState === 'visible' && void poll(ticket);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [ticket, waiting, poll]);

  // Count down to the room, then go there with the ticket's Prolific ID (R34).
  const room = view?.kind === 'starting' ? view.room : null;
  useEffect(() => {
    if (!room) return;
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [room]);
  useEffect(() => {
    if (!reply || reply.view.kind !== 'starting' || !reply.view.room) return;
    if (remainingMs(reply.view.starts_in_ms, reply.at, now) > 0) return;
    window.location.assign(
      reply.participant_id
        ? roomUrl(reply.view.room, reply.participant_id, window.location.search)
        : soloRoomUrl(reply.view.room, pid, window.location.search),
    );
  }, [reply, now, pid]);

  // Title, tone and focus on entering a state (R39).
  useEffect(() => {
    if (!view) return;
    const a = attention(prevKind.current, view.kind);
    prevKind.current = view.kind;
    if (!a) return;
    document.title = a.title;
    if (a.tone && audio.current) beep(audio.current);
    if (a.focus) actionRef.current?.focus();
  }, [view]);

  async function join(id = pid) {
    setError('');
    if (!audio.current && typeof AudioContext !== 'undefined') audio.current = new AudioContext();
    setLoading(true);
    try {
      const body = {
        token,
        // The study link's ID, if it has one; it is checked at the ready check.
        ...(group && normalizeProlificId(id) ? { participant_id: id } : {}),
        ...(needsPassword ? { password } : {}),
      };
      const { res, data } = await post('/api/start-link', body);
      if (res.ok && data.url) {
        // The ID just typed, and the rest of the study link's query.
        window.location.assign(soloRoomUrl(data.roomName, id, window.location.search));
        return; // keep the spinner while the browser navigates
      }
      if (res.ok && data.ticket) {
        sessionStorage.setItem(key, data.ticket);
        apply(data);
        setLoading(false);
        return;
      }
      if (res.status === 410) {
        setScreen('closed');
        setLoading(false);
        return;
      }
      setError(data.error || 'Failed to start the meeting — please try again');
    } catch {
      setError('Network error — please try again');
    }
    setScreen('form');
    setLoading(false);
  }

  /** The valid ID, or says what is wrong with it and puts the cursor there. */
  function checkedId() {
    const id = normalizeProlificId(pid);
    setIdError(id ? '' : BAD_ID);
    if (!id) document.getElementById('prolific-id')?.focus();
    return id;
  }

  async function onAction(action: Action) {
    if (!reply) return;
    if (action === 'join_again') {
      sessionStorage.removeItem(key);
      const id = reply.participant_id ?? pid;
      setPid(id);
      void join(id);
    } else if (action === 'ready') {
      const id = checkedId();
      if (!id) return;
      const { res, data } = await post('/api/start-link/ready', {
        ticket: reply.ticket,
        participant_id: id,
      }).catch(() => ({ res: null, data: null }));
      if (res?.ok) apply(data);
      else if (res?.status === 409) setIdError(ID_IN_USE);
      else void poll(reply.ticket);
    }
  }

  if (screen === 'closed') {
    return (
      <Page>
        <div className="space-y-3">
          <h1 className="text-5xl font-bold tracking-tight">{COPY.closedHeading}</h1>
          <p className="text-muted-foreground text-base">{COPY.closedMessage}</p>
        </div>
      </Page>
    );
  }
  if (screen === 'ticket' && reply) {
    return (
      <Page>
        <WaitingPanel
          w={waitingView(reply.view, reply.at, now)}
          lost={failures >= 2}
          onAction={(a) => void onAction(a)}
          actionRef={actionRef}
          id={{ value: pid, error: idError, onChange: setPid }}
        />
      </Page>
    );
  }

  return (
    <Page>
      <div className="space-y-3">
        <h1 className="text-5xl font-bold tracking-tight">
          {group ? COPY.startHeading : 'Join the meeting'}
        </h1>
        <p className="text-muted-foreground text-base">
          {group
            ? COPY.startMessage
            : 'A new meeting room opens for you, with an assistant already in it.'}
        </p>
      </div>
      <form
        className="space-y-5"
        onSubmit={(e) => {
          e.preventDefault();
          // Size 1 has no ready check: a valid ID and this button are the ready step.
          if (group || checkedId()) void join();
        }}
      >
        {!group && <ProlificIdField value={pid} error={idError} onChange={setPid} />}
        {needsPassword && (
          <div className="space-y-1.5">
            <label htmlFor="password" className="text-sm font-medium">
              {COPY.passwordLabel}
            </label>
            <input
              id="password"
              type="password"
              className={inputClass}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-describedby="password-help"
            />
            <p id="password-help" className="text-muted-foreground text-sm">
              {COPY.passwordHelp}
            </p>
          </div>
        )}
        <button type="submit" disabled={loading} className={buttonClass}>
          {group
            ? loading
              ? 'Joining…'
              : 'Join'
            : loading
              ? 'Setting up your meeting…'
              : 'Join meeting'}
        </button>
      </form>
      {error && (
        <p role="alert" className="text-signal text-sm">
          {error}
        </p>
      )}
    </Page>
  );
}
