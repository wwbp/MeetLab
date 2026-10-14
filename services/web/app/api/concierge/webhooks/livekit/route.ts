import { NextResponse } from 'next/server';
import {
  clearBotTrackSubscriptionSignalsForRoom,
  recordBotTrackSubscriptionSignal,
} from '@/lib/concierge/bot-track-subscription-store';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getWebhookReceiver, mapWebhookEvent } from '@/lib/concierge/livekit-admin';
import type { ConciergeEvent } from '@/lib/concierge/types';
import { getServerConfig } from '@/lib/config/server';
import { webhookSeverity } from '@/lib/concierge/event-log';

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

// Which bot a room has is the runner's session row (iteration 9); a webhook only
// clears this process's track-subscription signals when the room's bot goes.
function maybeClearTrackSignals(event: ConciergeEvent): void {
  if (!event.roomName) return;
  const name = event.event.toLowerCase();
  const botLeft =
    (name.includes('participant_left') || name.includes('participant_connection_aborted')) &&
    (event.participantIdentity?.trim() ?? '').startsWith('bot_');
  if (name.includes('room_finished') || botLeft) clearBotTrackSubscriptionSignalsForRoom(event.roomName);
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

    const storedEvent = pushConciergeEvent(mapWebhookEvent(event), webhookSeverity(event));
    maybeRecordTrackSubscriptionSignal(storedEvent);
    maybeClearTrackSignals(storedEvent);

    const { botRunnerUrl, botRunnerSecret } = getServerConfig();
    if (botRunnerUrl) {
      const payload: Record<string, unknown> = {
        type: storedEvent.event,
        room_name: storedEvent.roomName ?? undefined,
        participant_identity: storedEvent.participantIdentity ?? undefined,
        received_at: storedEvent.receivedAt,
      };
      // Forward the full webhook payload for egress events so agent-runner can
      // update MediaFile status when a recording finishes.
      if (storedEvent.event.toLowerCase().includes('egress')) {
        payload.payload = storedEvent.payload;
      }
      const fwdHeaders: Record<string, string> = { 'Content-Type': 'application/json' };
      if (botRunnerSecret) fwdHeaders['Authorization'] = `Bearer ${botRunnerSecret}`;
      fetch(`${botRunnerUrl}events`, {
        method: 'POST',
        headers: fwdHeaders,
        body: JSON.stringify(payload),
      }).catch(() => {
        // fire-and-forget: log nothing, never throws into the webhook response
      });
    }

    return NextResponse.json({ ok: true, event: storedEvent }, { headers: noStoreHeaders() });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : 'Failed to verify and process webhook event';
    return NextResponse.json({ error: message }, { status: 401, headers: noStoreHeaders() });
  }
}
