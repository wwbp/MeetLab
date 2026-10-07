// RTVI between the browser and the bot (Pipecat's protocol; design plan iteration 10, F13).
// The page says client-ready when it joins, and chat reaches the bot as send-text (the bot
// keeps it as the typing person's turn: agent-runner/rtvi.py). The bot sends the room only
// bot-ready and who is speaking.

const RTVI_LABEL = 'rtvi-ai';
export const RTVI_VERSION = '2.1.0'; // Pipecat 1.12's protocol

type RtviMessage = { label: string; type: string; id: string; data?: unknown } & Record<string, unknown>;

const encode = (msg: object) => new TextEncoder().encode(JSON.stringify(msg));

export const clientReady = (id: string = crypto.randomUUID()) =>
  encode({ label: RTVI_LABEL, type: 'client-ready', id, data: { version: RTVI_VERSION, about: { library: 'meetlab' } } });

// LiveKit's chat hands its legacy message ({id, timestamp, message, ignoreLegacy}) to this
// encoder and sends the result as its one data packet: the same fields, plus the RTVI envelope.
export const chatEncoder = (msg: { id: string; timestamp: number; message: string; ignoreLegacy?: boolean }) =>
  encode({ ...msg, label: RTVI_LABEL, type: 'send-text', data: { content: msg.message } });

export function decode(bytes: Uint8Array): RtviMessage | null {
  try {
    const msg = JSON.parse(new TextDecoder().decode(bytes));
    return msg && typeof msg === 'object' && msg.label === RTVI_LABEL && typeof msg.type === 'string' ? msg : null;
  } catch {
    return null;
  }
}
