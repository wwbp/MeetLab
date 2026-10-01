import { NextResponse } from 'next/server';
import { releaseBotRoomClaim } from '@/lib/concierge/bot-room-claim-store';
import { callBotRunnerStop } from '@/lib/concierge/bot-runner';
import { clearBotTrackSubscriptionSignalsForRoom } from '@/lib/concierge/bot-track-subscription-store';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getRoomServiceClient } from '@/lib/concierge/livekit-admin';

export const dynamic = 'force-dynamic';

function parseRoomName(value: string): string {
  return value.trim();
}

function toErrorStatus(message: string): number {
  return message.toLowerCase().includes('not found') ? 404 : 500;
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

    // Close the room's bot session now, not when the disconnected bot gets round to
    // it: a room recreated straight away would otherwise be handed the old, still
    // 'running' session (one running session per room). Best-effort: deleting the
    // room disconnects the bot regardless.
    await callBotRunnerStop(roomName);

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
