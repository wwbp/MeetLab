import { NextResponse } from 'next/server';
import { addBotRequest } from '@/lib/concierge/bot-requests-store';
import { claimBotRoom } from '@/lib/concierge/bot-room-claim-store';
import { callBotRunnerStart, createBotIdentity } from '@/lib/concierge/bot-runner';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { getRoomServiceClient } from '@/lib/concierge/livekit-admin';
import { getServerConfig, requireEnv } from '@/lib/config/server';
import { checkRateLimit, getClientIp } from '@/lib/rate-limit';
import { generateStartRoomName, pickUniform, verifyStartLink } from '@/lib/start-link';

export const dynamic = 'force-dynamic';

// Provisioning spins up a room + a GPU-backed bot — keep the public window tight.
const RATE_LIMIT_MAX = 5;
const RATE_LIMIT_WINDOW_MS = 60_000;

function runnerConfigHeaders(secret?: string): HeadersInit {
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  if (secret) headers['Authorization'] = `Bearer ${secret}`;
  return headers;
}

/** Copy the chosen scope's effective config onto the new room's scope so the bot
 *  (which resolves config by room name) picks it up. A deleted scope silently
 *  degrades to the global config via the runner's GET fallback — accepted. */
async function copyConfigToRoom(chosenScope: string, roomName: string): Promise<void> {
  const config = getServerConfig();
  const botRunnerUrl = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL');
  const base = botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`;
  const headers = runnerConfigHeaders(config.botRunnerSecret);

  const getRes = await fetch(`${base}config?room=${encodeURIComponent(chosenScope)}`, {
    headers,
    cache: 'no-store',
  });
  if (!getRes.ok) throw new Error(`config read failed (${getRes.status})`);
  const fields = await getRes.json();

  // PUT validation only accepts stt_vad_mode='local'; drop legacy values.
  if (fields.stt_vad_mode !== 'local') delete fields.stt_vad_mode;

  const putRes = await fetch(`${base}config`, {
    method: 'PUT',
    headers,
    body: JSON.stringify({ ...fields, scope: roomName }),
  });
  if (!putRes.ok) throw new Error(`config write failed (${putRes.status})`);
}

/** Public endpoint: one click on a start link → fresh room + bot with a config
 *  uniformly picked from the link's pool. POST-only so link unfurlers/prefetchers
 *  (which GET) can never provision anything. */
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  const token = typeof body?.token === 'string' ? body.token : '';
  const pool = await verifyStartLink(token);
  if (!pool) {
    return NextResponse.json({ error: 'Invalid start link' }, { status: 400 });
  }

  const ip = getClientIp(request);
  const limit = checkRateLimit(`start-link:${ip}`, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW_MS);
  if (!limit.allowed) {
    return NextResponse.json(
      { error: 'Too many meetings started — try again shortly' },
      { status: 429, headers: { 'Retry-After': String(Math.ceil(limit.retryAfterMs / 1000)) } }
    );
  }

  const chosenScope = pickUniform(pool);
  const roomName = generateStartRoomName();

  try {
    await copyConfigToRoom(chosenScope, roomName);

    const roomService = getRoomServiceClient();
    await roomService.createRoom({ name: roomName });

    // Fresh unique room → no contention; skip the concierge start-lock/409 dance but
    // register the claim/request/event so the console and webhook cleanup see it.
    const botIdentity = createBotIdentity(roomName);
    const runnerCall = await callBotRunnerStart(roomName, botIdentity);
    if (!runnerCall.ok) {
      throw new Error(runnerCall.errorText ?? `Bot runner returned ${runnerCall.status}`);
    }

    claimBotRoom(roomName, runnerCall.payload?.bot_identity ?? botIdentity);
    addBotRequest({
      roomName,
      status: 'started',
      botIdentity: runnerCall.payload?.bot_identity ?? botIdentity,
      runnerSessionId: runnerCall.payload?.session_id,
    });
    pushConciergeEvent({
      source: 'concierge',
      event: 'concierge.bot.started',
      roomName,
      payload: { via: 'start-link', chosenScope },
    });

    return NextResponse.json({ url: `/rooms/${encodeURIComponent(roomName)}`, roomName });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to start meeting';
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
