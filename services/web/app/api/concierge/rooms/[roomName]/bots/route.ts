import { NextResponse } from 'next/server';
import { callBotRunnerStart, createBotIdentity, getRoomSession } from '@/lib/concierge/bot-runner';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders, randomId } from '@/lib/concierge/http-utils';
import type { ConciergeBotRequest } from '@/lib/concierge/types';
import { getRoomServiceClient, isBotParticipant, mapParticipant } from '@/lib/concierge/livekit-admin';
import { noteRouteError } from '@/lib/concierge/event-log';

export const dynamic = 'force-dynamic';

// Iteration 9: meet keeps no claim, lock or request history. The runner's session row
// says which bot a room has, and Postgres allows one running session per room.
function botRequest(fields: Omit<ConciergeBotRequest, 'id' | 'requestedAt'>): ConciergeBotRequest {
  return { id: randomId(), requestedAt: new Date().toISOString(), ...fields };
}

function shouldForceRunnerFailure(request: Request): boolean {
  if (process.env.NODE_ENV === 'production') {
    return false;
  }
  return request.headers.get('x-concierge-test-force-runner-failure') === '1';
}

async function listActiveBots(roomName: string): Promise<
  Array<{
    identity: string;
    name?: string;
    state?: string;
    joinedAt?: string;
    trackCount: number;
  }>
> {
  const roomService = getRoomServiceClient();
  const participants = await roomService.listParticipants(roomName);
  return participants
    .map(mapParticipant)
    .filter(isBotParticipant)
    .map((bot) => ({
      identity: bot.identity,
      name: bot.name,
      state: bot.state,
      joinedAt: bot.joinedAt,
      trackCount: bot.tracks.length,
    }));
}

export async function GET(_request: Request, context: { params: Promise<{ roomName: string }> }) {
  try {
    const params = await context.params;
    const roomName = params.roomName.trim();
    if (!roomName) {
      return NextResponse.json(
        { error: 'Room name is required' },
        { status: 400, headers: noStoreHeaders() }
      );
    }

    const [bots, session] = await Promise.all([listActiveBots(roomName), getRoomSession(roomName)]);

    return NextResponse.json(
      { roomName, bots, assignedBotIdentity: session?.bot_identity },
      { headers: noStoreHeaders() }
    );
  } catch (error) {
    noteRouteError('GET /api/concierge/rooms/[roomName]/bots', error);
    const message = error instanceof Error ? error.message : 'Failed to list bots for room';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}

export async function POST(request: Request, context: { params: Promise<{ roomName: string }> }) {
  try {
    const params = await context.params;
    const roomName = params.roomName.trim();
    if (!roomName) {
      return NextResponse.json(
        { error: 'Room name is required' },
        { status: 400, headers: noStoreHeaders() }
      );
    }

    const body = await request.json().catch(() => ({}));
    const agentName =
      typeof body.agentName === 'string' && body.agentName.trim()
        ? body.agentName.trim()
        : undefined;

    const existingBots = await listActiveBots(roomName);
    if (existingBots.length > 0) {
      const failed = botRequest({ roomName, status: 'failed', agentName, error: 'Room already has an active bot participant' });
      return NextResponse.json(
        { error: failed.error, request: failed, activeBot: existingBots[0] },
        { status: 409, headers: noStoreHeaders() }
      );
    }

    const requestedBotIdentity = createBotIdentity(roomName);
    const runnerCall = shouldForceRunnerFailure(request)
      ? { ok: false, status: 503, errorText: 'Forced bot runner failure for concierge reliability test' }
      : await callBotRunnerStart(roomName, requestedBotIdentity, agentName);
    if (!runnerCall.ok) {
      const failed = botRequest({
        roomName,
        status: 'failed',
        agentName,
        botIdentity: requestedBotIdentity,
        error: runnerCall.errorText ?? `Bot runner returned ${runnerCall.status}`,
      });
      pushConciergeEvent({
        source: 'concierge',
        event: 'concierge.bot.start_failed',
        roomName,
        payload: { requestId: failed.id, status: runnerCall.status, error: failed.error, botIdentity: requestedBotIdentity },
      });
      return NextResponse.json({ error: failed.error, request: failed }, { status: 502, headers: noStoreHeaders() });
    }

    const payload = 'payload' in runnerCall ? runnerCall.payload : undefined;
    if (payload?.already_running) {
      const failed = botRequest({
        roomName,
        status: 'failed',
        agentName,
        botIdentity: payload.bot_identity,
        runnerSessionId: payload.session_id,
        error: 'A bot is already assigned to this room',
      });
      return NextResponse.json({ error: failed.error, request: failed }, { status: 409, headers: noStoreHeaders() });
    }

    const started = botRequest({
      roomName,
      status: 'started',
      agentName,
      botIdentity: payload?.bot_identity ?? requestedBotIdentity,
      runnerSessionId: payload?.session_id,
    });
    pushConciergeEvent({
      source: 'concierge',
      event: 'concierge.bot.started',
      roomName,
      payload: { requestId: started.id, runnerSessionId: started.runnerSessionId, agentName, botIdentity: started.botIdentity },
    });
    return NextResponse.json({ request: started }, { headers: noStoreHeaders() });
  } catch (error) {
    noteRouteError('POST /api/concierge/rooms/[roomName]/bots', error);
    const message = error instanceof Error ? error.message : 'Failed to start bot';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
