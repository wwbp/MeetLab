// RTVI between the browser and the bot (lib/rtvi.ts; design plan iteration 10, F13): chat
// reaches the bot as RTVI send-text, and the page tells the bot it is ready (client-ready).
import { describe, expect, it } from 'vitest';
import { RTVI_VERSION, chatEncoder, clientReady, decode } from './rtvi';

describe('rtvi', () => {
  it('sends chat as send-text and keeps LiveKit’s own chat fields for other browsers', () => {
    const msg = decode(chatEncoder({ id: 'm1', timestamp: 1, message: 'hello bot', ignoreLegacy: true }));
    expect(msg).toMatchObject({ label: 'rtvi-ai', type: 'send-text', data: { content: 'hello bot' } });
    expect(msg).toMatchObject({ id: 'm1', message: 'hello bot', ignoreLegacy: true });
  });

  it('says client-ready with the protocol version Pipecat checks', () => {
    expect(decode(clientReady())).toMatchObject({ label: 'rtvi-ai', type: 'client-ready', data: { version: RTVI_VERSION } });
  });

  it('ignores anything that is not RTVI', () => {
    const bytes = (s: string) => new TextEncoder().encode(s);
    for (const junk of ['5', 'not json', '{"message":"hi"}', '{"label":"other","type":"x"}']) {
      expect(decode(bytes(junk))).toBeNull();
    }
  });
});
