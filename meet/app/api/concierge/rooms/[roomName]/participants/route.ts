import { NextResponse } from 'next/server';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getRoomServiceClient, mapParticipant } from '@/lib/concierge/livekit-admin';
import { diffRoomPresence } from '@/lib/concierge/participant-presence-store';

export const dynamic = 'force-dynamic';

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
    const participants = await roomService.listParticipants(roomName);
    const mappedParticipants = participants.map(mapParticipant);

    const { joined, left } = diffRoomPresence(
      roomName,
      mappedParticipants.map((participant) => participant.identity)
    );
    for (const identity of joined) {
      pushConciergeEvent({
        source: 'concierge',
        event: 'concierge.participant.joined_observed',
        roomName,
        participantIdentity: identity,
      });
    }
    for (const identity of left) {
      pushConciergeEvent({
        source: 'concierge',
        event: 'concierge.participant.left_observed',
        roomName,
        participantIdentity: identity,
      });
    }

    return NextResponse.json(
      {
        roomName,
        participants: mappedParticipants,
      },
      { headers: noStoreHeaders() }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to list participants';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
