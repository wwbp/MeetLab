import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { waitingView } from '@/lib/waiting';
import { StartClient, WaitingPanel, firstScreen } from './StartClient';

const T0 = 1_000_000;
const panel = (view: Parameters<typeof waitingView>[0]) =>
  renderToStaticMarkup(
    <WaitingPanel w={waitingView(view, T0, T0)} lost={false} onAction={() => {}} />,
  );

describe('first screen', () => {
  it('a ticket stored in this tab resumes, even on a stopped link (R25)', () => {
    expect(firstScreen('6f1c2b3a-4d5e-4f60-8a7b-9c0d1e2f3a4b', false)).toBe('resume');
    expect(firstScreen(null, false)).toBe('closed');
    expect(firstScreen(null, true)).toBe('form');
  });
});

describe('Prolific ID at the ready check', () => {
  const ready = { kind: 'ready_check', deadline_in_ms: 60_000, pressed: false } as const;
  const render = (error?: string) =>
    renderToStaticMarkup(
      <WaitingPanel
        w={waitingView(ready, T0, T0)}
        lost={false}
        onAction={() => {}}
        id={{ value: '5f8e2a1b3c4d5e6f7a8b9c0d', error, onChange: () => {} }}
      />,
    );

  it('the field comes before the Ready button, prefilled', () => {
    const html = render();
    expect(html.indexOf('Prolific ID</label>')).toBeGreaterThan(-1);
    expect(html.indexOf('id="prolific-id"')).toBeLessThan(html.indexOf('I&#x27;m ready'));
    expect(html).toContain('value="5f8e2a1b3c4d5e6f7a8b9c0d"');
  });

  it('shows what is wrong with the ID', () => {
    expect(render('That is not it')).toContain('That is not it');
  });

  it('not after Ready is pressed, nor while searching', () => {
    for (const view of [
      { ...ready, pressed: true },
      { kind: 'searching', found: 1, size: 2, expires_in_ms: 1000, requeued: false },
    ] as const) {
      const html = renderToStaticMarkup(
        <WaitingPanel
          w={waitingView(view, T0, T0)}
          lost={false}
          onAction={() => {}}
          id={{ value: '', onChange: () => {} }}
        />,
      );
      expect(html).not.toContain('prolific-id');
    }
  });

  it('the start page of a group link no longer asks for the ID', () => {
    const html = renderToStaticMarkup(
      <StartClient token="t" linkId="l" size={2} needsPassword={false} active />,
    );
    expect(html).not.toContain('prolific-id');
    expect(html).toContain('Join');
  });
});

describe('WaitingPanel (R39)', () => {
  it('one polite region holds heading and message; the ready-check message is an alert', () => {
    const html = panel({ kind: 'ready_check', deadline_in_ms: 20_000, pressed: false });
    expect(html).toMatch(
      /<div aria-live="polite"[^>]*><h1[^>]*>Your group is ready<\/h1><p role="alert"[^>]*>Check your Prolific ID, then press the button within 20 seconds.<\/p>/,
    );
    expect(html).toContain('>I&#x27;m ready</button>');
  });

  it('countdown digits are hidden from screen readers, which hear "Starting in N seconds"', () => {
    const html = panel({ kind: 'starting', room: 'link-a', starts_in_ms: 8_000 });
    expect(html).toContain('<span aria-hidden="true"');
    expect(html).toContain('0:08</span>');
    expect(html).toContain('<span class="sr-only">Starting in 8 seconds</span>');
  });

  it('expired sends them back to the survey: no code, no button', () => {
    const html = panel({ kind: 'expired' });
    expect(html).toContain('Go back to the survey');
    expect(html).not.toContain('</button>');
  });

  it('missed offers Join again; lost connection is an alert', () => {
    const html = renderToStaticMarkup(
      <WaitingPanel w={waitingView({ kind: 'missed' }, T0, T0)} lost onAction={() => {}} />,
    );
    expect(html).toContain('>Join again</button>');
    expect(html).toContain(
      '<p role="alert" class="text-signal text-sm">Connection lost. Trying again…</p>',
    );
  });
});

describe('StartClient form', () => {
  it('a group link asks only for a labelled password', () => {
    const html = renderToStaticMarkup(
      <StartClient token="t" linkId="Qm9vdHN0cmFwTGlua0lEMQ" size={3} needsPassword active />,
    );
    expect(html).toContain('Join the group meeting');
    expect(html).not.toContain('prolific-id');
    expect(html).toMatch(/<label for="password"[^>]*>Meeting password<\/label>/);
    expect(html).toMatch(
      /<input[^>]*id="password"[^>]*type="password"|<input[^>]*type="password"[^>]*id="password"/,
    );
  });

  it("a size-1 link is today's page: no ID, no password", () => {
    const html = renderToStaticMarkup(
      <StartClient
        token="t"
        linkId="x3Jd8kq0VbN2mT5wYc7HfA"
        size={1}
        needsPassword={false}
        active
      />,
    );
    expect(html).toContain('Join the meeting');
    expect(html).not.toContain('prolific-id');
    expect(html).not.toContain('password');
  });

  it('a size-1 link with a password asks for it, still no ID (R27)', () => {
    const html = renderToStaticMarkup(
      <StartClient token="t" linkId="x3Jd8kq0VbN2mT5wYc7HfA" size={1} needsPassword active />,
    );
    expect(html).toContain('Join the meeting');
    expect(html).not.toContain('prolific-id');
    expect(html).toMatch(/<label for="password"[^>]*>Meeting password<\/label>/);
  });
});
