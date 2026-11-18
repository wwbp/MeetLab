import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { DisconnectReason } from 'livekit-client';
import { completionCodeWithRetries, endsStudy } from '@/lib/join';
import { AfterCall, CompletionView, DroppedScreen } from './AfterCall';

// next/font only exists inside a Next build.
vi.mock('@/lib/fonts', () => ({ inter: { className: 'inter' } }));

const GET_CODE = 'The meeting is over: get my completion code';
const FAILURE = 'We could not confirm you took part yet';

// What the room page shows once the room reports this disconnect (handleOnLeave).
const afterDisconnect = (reason?: DisconnectReason) =>
  renderToStaticMarkup(
    <AfterCall
      hasLeft={endsStudy(reason)}
      dropped={!endsStudy(reason)}
      roomName="room-1"
      prolificId="5f2a1b3c4d5e6f7a8b9c0d1e"
      onGetCode={() => {}}
      onDone={() => {}}
    />,
  );

const view = (code: string | null, failed = code === null) =>
  renderToStaticMarkup(
    <CompletionView
      code={code}
      failed={failed}
      copied={false}
      onCopy={() => {}}
      onRetry={() => {}}
      onDone={() => {}}
    />,
  );

/** The onClick of the button with this label, found in a hook-free component's output. */
function buttonClick(node: React.ReactNode, label: string): (() => void) | undefined {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = buttonClick(child, label);
      if (found) return found;
    }
  }
  if (!React.isValidElement(node)) return undefined;
  const props = node.props as { children?: React.ReactNode; onClick?: () => void };
  if (node.type === 'button' && props.children === label) return props.onClick;
  return buttonClick(props.children, label);
}

describe('when the call ends', () => {
  it('the meeting being closed (ROOM_DELETED) shows the completion screen', () => {
    const html = afterDisconnect(DisconnectReason.ROOM_DELETED);
    expect(html).toContain('Thank you for taking part');
    expect(html).not.toContain('Rejoin');
  });

  it('a dropped connection shows Rejoin and the get-my-code action, both buttons', () => {
    const html = afterDisconnect(DisconnectReason.STATE_MISMATCH);
    expect(html).toContain('You were disconnected');
    expect(html).toMatch(/<button[^>]*>Rejoin<\/button>/);
    expect(html).toContain(`>${GET_CODE}</button>`);
    expect(html).not.toContain('Thank you for taking part');
  });

  it('pressing get-my-code asks for the completion screen, which then replaces Rejoin', () => {
    const onGetCode = vi.fn();
    buttonClick(DroppedScreen({ onGetCode }), GET_CODE)!();
    expect(onGetCode).toHaveBeenCalledOnce();
    // The page answers by setting hasLeft; dropped stays true.
    const html = renderToStaticMarkup(
      <AfterCall hasLeft dropped roomName="r" prolificId="p" onGetCode={() => {}} onDone={() => {}} />,
    );
    expect(html).toContain('Thank you for taking part');
    expect(html).not.toContain('Rejoin');
  });
});

describe('the completion code', () => {
  it('a request that fails, then succeeds, shows the code without a click', async () => {
    const ask = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(404)
      .mockRejectedValueOnce(502)
      .mockResolvedValue('ABCD2345');
    const waits: number[] = [];
    const code = await completionCodeWithRetries(ask, async (ms) => void waits.push(ms));
    expect(ask).toHaveBeenCalledTimes(3);
    expect(waits).toEqual([2000, 2000]);

    const html = view(code);
    expect(html).toContain('ABCD2345');
    expect(html).toContain('Copy code');
    expect(html).not.toContain(FAILURE);
    expect(html).not.toContain('Try again');
  });

  it('failing every attempt shows the failure text and Try again', async () => {
    const ask = vi.fn<() => Promise<string>>().mockRejectedValue(404);
    const waits: number[] = [];
    const code = await completionCodeWithRetries(ask, async (ms) => void waits.push(ms));
    expect(code).toBeNull();
    expect(ask).toHaveBeenCalledTimes(5);
    expect(waits).toEqual([2000, 2000, 2000, 2000]);

    const html = view(code);
    expect(html).toContain(FAILURE);
    expect(html).toContain('>Try again</button>');
    expect(html).not.toContain('Copy code');
  });

  it('while attempts are still running: no failure text yet', () => {
    const html = view(null, false);
    expect(html).not.toContain(FAILURE);
    expect(html).not.toContain('Try again');
  });
});
