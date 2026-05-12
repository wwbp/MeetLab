import { NextResponse } from 'next/server';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getRoomServiceClient } from '@/lib/concierge/livekit-admin';

export const dynamic = 'force-dynamic';

export async function POST(
  request: Request,
  context: { params: Promise<{ roomName: string; identity: string; trackSid: string }> }
) {
  try {
    const params = await context.params;
    const roomName = params.roomName.trim();
    const identity = params.identity.trim();
    const trackSid = params.trackSid.trim();
    if (!roomName || !identity || !trackSid) {
      return NextResponse.json(
        { error: 'Room name, participant identity, and track sid are required' },
        { status: 400, headers: noStoreHeaders() }
      );
    }

    const body = await request.json().catch(() => ({}));
    if (typeof body.muted !== 'boolean') {
      return NextResponse.json(
        { error: '`muted` must be a boolean' },
        { status: 400, headers: noStoreHeaders() }
      );
    }

    const roomService = getRoomServiceClient();
    await roomService.mutePublishedTrack(roomName, identity, trackSid, body.muted);

    pushConciergeEvent({
      source: 'concierge',
      event: body.muted ? 'concierge.track.muted' : 'concierge.track.unmuted',
      roomName,
      participantIdentity: identity,
      payload: { trackSid },
    });

    return NextResponse.json(
      { ok: true, muted: body.muted, roomName, identity, trackSid },
      { headers: noStoreHeaders() }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to update track mute state';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
