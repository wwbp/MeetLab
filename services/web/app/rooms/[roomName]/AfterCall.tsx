'use client';
import React from 'react';
import { inter } from '@/lib/fonts';
import { completionCodeWithRetries } from '@/lib/join';

// Participant screens, Swiss style on LiveKit's dark theme: one typeface, left-aligned, red for problems.
export const SIGNAL = 'oklch(0.68 0.2 27)';

/** What replaces the call once it is over: the completion code, or Rejoin after a drop. */
export function AfterCall(props: {
  hasLeft: boolean;
  dropped: boolean;
  roomName: string;
  prolificId: string;
  onGetCode: () => void;
  onDone: () => void;
}) {
  if (props.dropped && !props.hasLeft) return <DroppedScreen onGetCode={props.onGetCode} />;
  return (
    <CompletionScreen roomName={props.roomName} prolificId={props.prolificId} onDone={props.onDone} />
  );
}

export function DroppedScreen(props: { onGetCode: () => void }) {
  return (
    <main
      data-lk-theme="default"
      className={inter.className}
      style={{ display: 'grid', placeItems: 'center', height: '100%', padding: '1rem' }}
    >
      <div style={{ display: 'grid', gap: '1.25rem', justifyItems: 'start', maxWidth: '32rem' }}>
        <h1 style={{ margin: 0, fontSize: '2.5rem', fontWeight: 700, letterSpacing: '-0.02em' }}>You were disconnected</h1>
        <p style={{ margin: 0, color: 'var(--lk-fg2)' }}>Your connection dropped. Rejoin to carry on with the conversation.</p>
        <button className="lk-button" onClick={() => window.location.reload()}>
          Rejoin
        </button>
        {/* The meeting may have ended while they were away: Rejoin would then be an empty room. */}
        <button className="lk-button" onClick={props.onGetCode}>
          The meeting is over: get my completion code
        </button>
      </div>
    </main>
  );
}

/**
 * What a participant sees after leaving. The code is the only thing on it,
 * because the only thing they have to do is paste it into the survey — the bot
 * says as much on its way out (bot_config.closing_message).
 *
 * Fetched now rather than handed out at join: the server only issues it to a
 * Prolific ID the bot heard in this room (/api/completion-code).
 */
function CompletionScreen(props: { roomName: string; prolificId: string; onDone: () => void }) {
  const [code, setCode] = React.useState<string | null>(null);
  const [failed, setFailed] = React.useState(false);
  const fetchCode = React.useCallback(() => {
    const q = new URLSearchParams({ roomName: props.roomName, prolificId: props.prolificId });
    completionCodeWithRetries(() =>
      fetch(`/api/completion-code?${q}`)
        .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
        .then((body) => body.completionCode),
    ).then((found) => (found ? setCode(found) : setFailed(true)));
  }, [props.roomName, props.prolificId]);
  React.useEffect(fetchCode, [fetchCode]);

  const [copied, setCopied] = React.useState(false);
  const copy = React.useCallback(() => {
    // Clipboard access is denied in some embedded contexts; the code is on screen
    // either way, so a failure just means they type it.
    if (!code) return;
    navigator.clipboard?.writeText(code).then(
      () => setCopied(true),
      () => setCopied(false),
    );
  }, [code]);

  return (
    <CompletionView
      code={code}
      failed={failed}
      copied={copied}
      onCopy={copy}
      onRetry={() => {
        setFailed(false);
        fetchCode();
      }}
      onDone={props.onDone}
    />
  );
}

export function CompletionView(props: {
  code: string | null;
  failed: boolean;
  copied: boolean;
  onCopy: () => void;
  onRetry: () => void;
  onDone: () => void;
}) {
  const { code, failed } = props;
  return (
    <main
      data-lk-theme="default"
      className={inter.className}
      style={{ display: 'grid', placeItems: 'center', height: '100%', padding: '1rem' }}
    >
      <div style={{ display: 'grid', gap: '1.25rem', justifyItems: 'start', maxWidth: '32rem' }}>
        <h1 style={{ margin: 0, fontSize: '2.5rem', fontWeight: 700, letterSpacing: '-0.02em' }}>Thank you for taking part</h1>
        <p style={{ margin: 0, color: 'var(--lk-fg2)' }}>Copy this code into the survey to finish the study.</p>
        <code
          style={{
            fontFamily: 'inherit',
            fontSize: '2.5rem',
            fontWeight: 700,
            letterSpacing: '0.2em',
            fontVariantNumeric: 'tabular-nums',
            padding: '0.75rem 0',
            borderTop: '1px solid var(--lk-fg)',
            borderBottom: '1px solid var(--lk-fg)',
            userSelect: 'all',
          }}
        >
          {code ?? '…'}
        </code>
        {failed && (
          <p style={{ margin: 0, color: SIGNAL }}>
            We could not confirm you took part yet. Try again in a moment, or contact the researcher.
          </p>
        )}
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {failed ? (
            <button className="lk-button" onClick={props.onRetry}>
              Try again
            </button>
          ) : (
            <button className="lk-button" onClick={props.onCopy} disabled={!code}>
              {props.copied ? 'Copied' : 'Copy code'}
            </button>
          )}
          <button className="lk-button" onClick={props.onDone}>
            Done
          </button>
        </div>
      </div>
    </main>
  );
}
