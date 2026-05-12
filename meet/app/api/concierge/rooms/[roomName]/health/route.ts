import { NextResponse } from 'next/server';
import { getBotRoomClaim } from '@/lib/concierge/bot-room-claim-store';
import { getLatestBotTrackSubscriptionSignal } from '@/lib/concierge/bot-track-subscription-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getRoomServiceClient, isBotParticipant, mapParticipant, mapRoom } from '@/lib/concierge/livekit-admin';
import type { ConciergeBotHealthStatus, ConciergeRoomHealthStatus } from '@/lib/concierge/types';

export const dynamic = 'force-dynamic';
const SUBSCRIPTION_SIGNAL_MAX_AGE_MS = 15 * 60 * 1000;

function computeOverallStatus(
  roomStatus: ConciergeRoomHealthStatus,
  botStatus: ConciergeBotHealthStatus
): 'ok' | 'degraded' | 'down' {
  if (roomStatus === 'missing') {
    return 'down';
  }
  if (botStatus === 'connected') {
    return 'ok';
  }
  return 'degraded';
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

    const roomService = getRoomServiceClient();
    const claim = getBotRoomClaim(roomName);
    const rooms = await roomService.listRooms([roomName]);
    const room = rooms.find((candidate) => candidate.name === roomName);
    if (!room) {
      const roomStatus: ConciergeRoomHealthStatus = 'missing';
      const botStatus: ConciergeBotHealthStatus = claim ? 'starting' : 'missing';
      return NextResponse.json(
        {
          roomName,
          checkedAt: new Date().toISOString(),
          overallStatus: computeOverallStatus(roomStatus, botStatus),
          room: {
            status: roomStatus,
            exists: false,
            numParticipants: 0,
          },
          bot: {
            status: botStatus,
            assignedIdentity: claim?.botIdentity,
            trackCount: 0,
            subscriptionSignal: {
              status: 'unknown',
            },
          },
        },
        { headers: noStoreHeaders() }
      );
    }

    const mappedRoom = mapRoom(room);
    const participants = await roomService.listParticipants(roomName);
    const mappedParticipants = participants.map(mapParticipant);
    const botParticipant = mappedParticipants.find(isBotParticipant);

    const roomStatus: ConciergeRoomHealthStatus =
      (mappedRoom.numParticipants ?? 0) > 0 ? 'active' : 'idle';
    const trackCount = botParticipant?.tracks.length ?? 0;
    const botStatus: ConciergeBotHealthStatus = botParticipant
      ? trackCount > 0
        ? 'connected'
        : 'connected_no_tracks'
      : claim
        ? 'starting'
        : 'missing';
    const trackedBotIdentity = botParticipant?.identity ?? claim?.botIdentity;
    const latestSubscriptionSignal = getLatestBotTrackSubscriptionSignal(
      roomName,
      trackedBotIdentity
    );
    const hasFreshSubscriptionSignal =
      !!latestSubscriptionSignal &&
      Date.now() - Date.parse(latestSubscriptionSignal.observedAt) <=
        SUBSCRIPTION_SIGNAL_MAX_AGE_MS;

    return NextResponse.json(
      {
        roomName,
        checkedAt: new Date().toISOString(),
        overallStatus: computeOverallStatus(roomStatus, botStatus),
        room: {
          status: roomStatus,
          exists: true,
          numParticipants: mappedRoom.numParticipants ?? 0,
          metadata: mappedRoom.metadata,
          creationTime: mappedRoom.creationTime,
        },
        bot: {
          status: botStatus,
          assignedIdentity: claim?.botIdentity,
          identity: botParticipant?.identity,
          state: botParticipant?.state,
          trackCount,
          subscriptionSignal: {
            status: trackedBotIdentity
              ? hasFreshSubscriptionSignal
                ? 'observed'
                : 'not_observed'
              : 'unknown',
            observedAt: latestSubscriptionSignal?.observedAt,
            trackSid: latestSubscriptionSignal?.trackSid,
            sourceEvent: latestSubscriptionSignal?.sourceEvent,
          },
        },
      },
      { headers: noStoreHeaders() }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to evaluate room health';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
