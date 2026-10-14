import { NextResponse } from 'next/server';
import { callBotRunnerStop, getRoomSession } from '@/lib/concierge/bot-runner';
import { clearBotTrackSubscriptionSignalsForRoom } from '@/lib/concierge/bot-track-subscription-store';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getRoomServiceClient } from '@/lib/concierge/livekit-admin';
import { noteRouteError } from '@/lib/concierge/event-log';

export const dynamic = 'force-dynamic';

function isNotFound(error: unknown): boolean {
  const e = error as { code?: unknown; status?: unknown; message?: unknown };
  return e?.code === 'not_found' || e?.status === 404 || /not.?found/i.test(String(e?.message ?? ''));
}

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
        { error: 'Room name and bot identity are required' },
        { status: 400, headers: noStoreHeaders() }
      );
    }

    const session = await getRoomSession(roomName);
    if (session && session.bot_identity !== identity) {
      return NextResponse.json(
        {
          error: `Bot identity mismatch: assigned bot is "${session.bot_identity}"`,
          assignedBotIdentity: session.bot_identity,
        },
        { status: 409, headers: noStoreHeaders() }
      );
    }

    // Stop at the runner first: removing the participant alone does nothing while the
    // bot is still starting, and it then joined anyway (acceptance stop_early).
    const stop = await callBotRunnerStop(roomName);
    if (!stop.ok) {
      return NextResponse.json(
        { error: `Bot runner could not stop the bot: ${stop.errorText ?? stop.status}` },
        { status: 502, headers: noStoreHeaders() }
      );
    }

    const roomService = getRoomServiceClient();
    try {
      await roomService.removeParticipant(roomName, identity);
    } catch (error) {
      if (!isNotFound(error)) throw error; // not joined yet: the runner already stopped it
    }
    clearBotTrackSubscriptionSignalsForRoom(roomName);

    pushConciergeEvent({
      source: 'concierge',
      event: 'concierge.bot.removed',
      roomName,
      participantIdentity: identity,
    });

    return new NextResponse(null, { status: 204, headers: noStoreHeaders() });
  } catch (error) {
    noteRouteError('DELETE /api/concierge/rooms/[roomName]/bots/[identity]', error);
    const message = error instanceof Error ? error.message : 'Failed to remove bot';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
