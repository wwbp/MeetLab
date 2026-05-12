import { NextResponse } from 'next/server';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getRoomServiceClient } from '@/lib/concierge/livekit-admin';

export const dynamic = 'force-dynamic';

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ roomName: string; identity: string }> }
) {
  try {
    const params = await context.params;
    const roomName = params.roomName.trim();
    const identity = params.identity.trim();
    if (!roomName || !identity) {
      return NextResponse.json(
        { error: 'Room name and participant identity are required' },
        { status: 400, headers: noStoreHeaders() }
      );
    }

    const roomService = getRoomServiceClient();
    await roomService.removeParticipant(roomName, identity);

    pushConciergeEvent({
      source: 'concierge',
      event: 'concierge.participant.removed',
      roomName,
      participantIdentity: identity,
    });

    return new NextResponse(null, { status: 204, headers: noStoreHeaders() });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to remove participant';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
