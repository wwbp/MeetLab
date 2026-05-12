import { NextResponse } from 'next/server';
import { addBotRequest, listBotRequestsForRoom } from '@/lib/concierge/bot-requests-store';
import {
  claimBotRoom,
  getBotRoomClaim,
  releaseBotRoomClaim,
} from '@/lib/concierge/bot-room-claim-store';
import { acquireBotStartLock, releaseBotStartLock } from '@/lib/concierge/bot-start-lock-store';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getRoomServiceClient, isBotParticipant, mapParticipant } from '@/lib/concierge/livekit-admin';
import { getServerConfig, requireEnv } from '@/lib/config/server';

export const dynamic = 'force-dynamic';

type BotRunnerResponse = {
  session_id?: string;
  bot_identity?: string;
  message?: string;
  error?: string;
};

function toBotRunnerStartUrl(botRunnerUrl: string): string {
  const normalized = botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`;
  return `${normalized}start`;
}

function roomSlug(roomName: string): string {
  const slug = roomName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug.slice(0, 24) || 'room';
}

function createBotIdentity(roomName: string): string {
  const suffix =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID().replace(/-/g, '').slice(0, 10)
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  return `bot_${roomSlug(roomName)}_${suffix}`;
}

function shouldForceRunnerFailure(request: Request): boolean {
  if (process.env.NODE_ENV === 'production') {
    return false;
  }
  return request.headers.get('x-concierge-test-force-runner-failure') === '1';
}

async function callBotRunnerStart(
  roomName: string,
  botIdentity: string,
  agentName?: string
): Promise<{
  ok: boolean;
  status: number;
  payload?: BotRunnerResponse;
  errorText?: string;
}> {
  const config = getServerConfig();
  const botRunnerUrl = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL');
  const endpoint = toBotRunnerStartUrl(botRunnerUrl);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);

  try {
    const body: {
      room_name: string;
      bot_identity: string;
      custom_data: { requested_by: string };
      room_config?: { agents: Array<{ agent_name: string }> };
    } = {
      room_name: roomName,
      bot_identity: botIdentity,
      custom_data: {
        requested_by: 'concierge',
      },
    };
    if (agentName) {
      body.room_config = {
        agents: [{ agent_name: agentName }],
      };
    }

    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    const text = await response.text();
    let payloadRaw: unknown;
    let payload: BotRunnerResponse | undefined;
    if (text) {
      try {
        payloadRaw = JSON.parse(text);
        if (typeof payloadRaw === 'object' && payloadRaw !== null && !Array.isArray(payloadRaw)) {
          payload = payloadRaw as BotRunnerResponse;
        }
      } catch {
        payloadRaw = undefined;
      }
    }

    const tupleError =
      Array.isArray(payloadRaw) &&
      payloadRaw.length === 2 &&
      typeof payloadRaw[1] === 'number' &&
      payloadRaw[1] >= 400
        ? payloadRaw
        : undefined;
    const tupleErrorMessage =
      tupleError &&
      typeof tupleError[0] === 'object' &&
      tupleError[0] !== null &&
      'error' in tupleError[0] &&
      typeof (tupleError[0] as { error?: unknown }).error === 'string'
        ? (tupleError[0] as { error: string }).error
        : undefined;

    const appLevelError =
      payload?.error ??
      tupleErrorMessage ??
      (tupleError ? `Bot runner returned application error ${tupleError[1]}` : undefined);
    const isSuccess = response.ok && !appLevelError;

    return {
      ok: isSuccess,
      status: response.status,
      payload,
      errorText: isSuccess ? undefined : (appLevelError ?? text),
    };
  } catch (error) {
    return {
      ok: false,
      status: 500,
      errorText: error instanceof Error ? error.message : 'Bot runner request failed',
    };
  } finally {
    clearTimeout(timeout);
  }
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

    const bots = await listActiveBots(roomName);

    return NextResponse.json(
      {
        roomName,
        bots,
        requests: listBotRequestsForRoom(roomName),
        assignedBotIdentity: getBotRoomClaim(roomName)?.botIdentity,
      },
      { headers: noStoreHeaders() }
    );
  } catch (error) {
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
      const failedRequest = addBotRequest({
        roomName,
        status: 'failed',
        agentName,
        error: 'Room already has an active bot participant',
      });
      return NextResponse.json(
        {
          error: failedRequest.error,
          request: failedRequest,
          activeBot: existingBots[0],
        },
        { status: 409, headers: noStoreHeaders() }
      );
    }

    const existingClaim = getBotRoomClaim(roomName);
    if (existingClaim) {
      const failedRequest = addBotRequest({
        roomName,
        status: 'failed',
        agentName,
        botIdentity: existingClaim.botIdentity,
        error: 'A bot is already assigned to this room',
      });
      return NextResponse.json(
        {
          error: failedRequest.error,
          request: failedRequest,
        },
        { status: 409, headers: noStoreHeaders() }
      );
    }

    if (!acquireBotStartLock(roomName)) {
      const failedRequest = addBotRequest({
        roomName,
        status: 'failed',
        agentName,
        error: 'A bot start request is already in progress for this room',
      });
      return NextResponse.json(
        { error: failedRequest.error, request: failedRequest },
        { status: 409, headers: noStoreHeaders() }
      );
    }

    const requestedBotIdentity = createBotIdentity(roomName);
    try {
      if (!claimBotRoom(roomName, requestedBotIdentity)) {
        const failedRequest = addBotRequest({
          roomName,
          status: 'failed',
          agentName,
          botIdentity: requestedBotIdentity,
          error: 'A bot is already assigned to this room',
        });
        return NextResponse.json(
          { error: failedRequest.error, request: failedRequest },
          { status: 409, headers: noStoreHeaders() }
        );
      }

      const runnerCall = shouldForceRunnerFailure(request)
        ? {
            ok: false,
            status: 503,
            errorText: 'Forced bot runner failure for concierge reliability test',
          }
        : await callBotRunnerStart(roomName, requestedBotIdentity, agentName);
      if (!runnerCall.ok) {
        const failedRequest = addBotRequest({
          roomName,
          status: 'failed',
          agentName,
          botIdentity: requestedBotIdentity,
          error: runnerCall.errorText ?? `Bot runner returned ${runnerCall.status}`,
        });
        releaseBotRoomClaim(roomName);

        pushConciergeEvent({
          source: 'concierge',
          event: 'concierge.bot.start_failed',
          roomName,
          payload: {
            requestId: failedRequest.id,
            status: runnerCall.status,
            error: failedRequest.error,
            botIdentity: requestedBotIdentity,
          },
        });

        return NextResponse.json(
          { error: failedRequest.error, request: failedRequest },
          { status: 502, headers: noStoreHeaders() }
        );
      }

      const startedRequest = addBotRequest({
        roomName,
        status: 'started',
        agentName,
        botIdentity: runnerCall.payload?.bot_identity ?? requestedBotIdentity,
        runnerSessionId: runnerCall.payload?.session_id,
      });

      pushConciergeEvent({
        source: 'concierge',
        event: 'concierge.bot.started',
        roomName,
        payload: {
          requestId: startedRequest.id,
          runnerSessionId: startedRequest.runnerSessionId,
          agentName,
          botIdentity: startedRequest.botIdentity,
        },
      });

      return NextResponse.json({ request: startedRequest }, { headers: noStoreHeaders() });
    } finally {
      releaseBotStartLock(roomName);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to start bot';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
