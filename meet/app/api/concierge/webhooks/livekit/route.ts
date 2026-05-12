import { NextResponse } from 'next/server';
import { getBotRoomClaim, releaseBotRoomClaim } from '@/lib/concierge/bot-room-claim-store';
import {
  clearBotTrackSubscriptionSignalsForRoom,
  recordBotTrackSubscriptionSignal,
} from '@/lib/concierge/bot-track-subscription-store';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getWebhookReceiver, mapWebhookEvent } from '@/lib/concierge/livekit-admin';
import type { ConciergeEvent } from '@/lib/concierge/types';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

function readTrackSidFromPayload(payload: unknown): string | undefined {
  if (typeof payload !== 'object' || payload === null) {
    return undefined;
  }
  const record = payload as Record<string, unknown>;
  if (typeof record.trackSid === 'string') {
    return record.trackSid;
  }
  if (typeof record.track_sid === 'string') {
    return record.track_sid;
  }
  if (typeof record.track === 'object' && record.track !== null) {
    const track = record.track as Record<string, unknown>;
    if (typeof track.sid === 'string') {
      return track.sid;
    }
  }
  return undefined;
}

function maybeRecordTrackSubscriptionSignal(event: ConciergeEvent): void {
  const eventName = event.event.toLowerCase();
  if (!eventName.includes('track_subscribed')) {
    return;
  }
  if (!event.roomName) {
    return;
  }

  recordBotTrackSubscriptionSignal({
    roomName: event.roomName,
    botIdentity:
      event.participantIdentity && event.participantIdentity.startsWith('bot_')
        ? event.participantIdentity
        : undefined,
    trackSid: readTrackSidFromPayload(event.payload),
    sourceEvent: event.event,
    observedAt: event.receivedAt,
  });
}

function maybeReconcileBotClaim(event: ConciergeEvent): void {
  if (!event.roomName) {
    return;
  }

  const eventName = event.event.toLowerCase();
  if (eventName.includes('room_finished')) {
    releaseBotRoomClaim(event.roomName);
    clearBotTrackSubscriptionSignalsForRoom(event.roomName);
    return;
  }

  if (
    !eventName.includes('participant_left') &&
    !eventName.includes('participant_connection_aborted')
  ) {
    return;
  }

  const claim = getBotRoomClaim(event.roomName);
  if (!claim) {
    return;
  }

  const participantIdentity = event.participantIdentity?.trim();
  if (!participantIdentity) {
    return;
  }

  if (participantIdentity === claim.botIdentity || participantIdentity.startsWith('bot_')) {
    releaseBotRoomClaim(event.roomName);
    clearBotTrackSubscriptionSignalsForRoom(event.roomName);
  }
}

export async function POST(request: Request) {
  try {
    const authHeader = request.headers.get('Authorization');
    if (!authHeader) {
      return NextResponse.json(
        { error: 'Missing Authorization header' },
        { status: 401, headers: noStoreHeaders() }
      );
    }

    const body = await request.text();
    const receiver = getWebhookReceiver();
    const event = await receiver.receive(body, authHeader);

    const storedEvent = pushConciergeEvent(mapWebhookEvent(event));
    maybeRecordTrackSubscriptionSignal(storedEvent);
    maybeReconcileBotClaim(storedEvent);
    return NextResponse.json({ ok: true, event: storedEvent }, { headers: noStoreHeaders() });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : 'Failed to verify and process webhook event';
    return NextResponse.json({ error: message }, { status: 401, headers: noStoreHeaders() });
  }
}
