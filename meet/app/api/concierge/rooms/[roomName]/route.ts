import { NextResponse } from 'next/server';
import { releaseBotRoomClaim } from '@/lib/concierge/bot-room-claim-store';
import { clearBotTrackSubscriptionSignalsForRoom } from '@/lib/concierge/bot-track-subscription-store';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getRoomServiceClient, mapRoom } from '@/lib/concierge/livekit-admin';

export const dynamic = 'force-dynamic';

function parseRoomName(value: string): string {
  return value.trim();
}

function toErrorStatus(message: string): number {
  return message.toLowerCase().includes('not found') ? 404 : 500;
}

export async function PATCH(request: Request, context: { params: Promise<{ roomName: string }> }) {
  try {
    const params = await context.params;
    const roomName = parseRoomName(params.roomName);
    if (!roomName) {
      return NextResponse.json(
        { error: 'Room name is required' },
        { status: 400, headers: noStoreHeaders() }
      );
    }

    const body = await request.json().catch(() => ({}));
    if (typeof body.metadata !== 'string') {
      return NextResponse.json(
        { error: 'metadata must be a string' },
        { status: 400, headers: noStoreHeaders() }
      );
    }
    if (body.metadata.length > 4096) {
      return NextResponse.json(
        { error: 'metadata must be 4096 characters or fewer' },
        { status: 400, headers: noStoreHeaders() }
      );
    }

    const roomService = getRoomServiceClient();
    const updatedRoom = await roomService.updateRoomMetadata(roomName, body.metadata);

    pushConciergeEvent({
      source: 'concierge',
      event: 'concierge.room.updated',
      roomName,
      payload: {
        metadata: body.metadata,
      },
    });

    return NextResponse.json(
      { room: mapRoom(updatedRoom) },
      { status: 200, headers: noStoreHeaders() }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to update room';
    return NextResponse.json(
      { error: message },
      { status: toErrorStatus(message), headers: noStoreHeaders() }
    );
  }
}

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ roomName: string }> }
) {
  try {
    const params = await context.params;
    const roomName = parseRoomName(params.roomName);
    if (!roomName) {
      return NextResponse.json(
        { error: 'Room name is required' },
        { status: 400, headers: noStoreHeaders() }
      );
    }

    const roomService = getRoomServiceClient();
    await roomService.deleteRoom(roomName);
    releaseBotRoomClaim(roomName);
    clearBotTrackSubscriptionSignalsForRoom(roomName);

    pushConciergeEvent({
      source: 'concierge',
      event: 'concierge.room.deleted',
      roomName,
    });

    return new NextResponse(null, { status: 204, headers: noStoreHeaders() });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to delete room';
    return NextResponse.json(
      { error: message },
      { status: toErrorStatus(message), headers: noStoreHeaders() }
    );
  }
}
