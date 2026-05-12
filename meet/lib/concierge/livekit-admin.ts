import { RoomServiceClient, WebhookReceiver } from 'livekit-server-sdk';
import type {
  ConciergeEvent,
  ConciergeParticipant,
  ConciergeRoom,
  ConciergeTrack,
} from '@/lib/concierge/types';
import { getServerConfig, requireEnv } from '@/lib/config/server';

type TrackLike = {
  sid?: string;
  name?: string;
  source?: unknown;
  type?: unknown;
  muted?: boolean;
  mimeType?: string;
  width?: number;
  height?: number;
};

type ParticipantLike = {
  identity?: string;
  sid?: string;
  name?: string;
  state?: unknown;
  metadata?: string;
  joinedAt?: unknown;
  permission?: { canPublish?: boolean } | undefined;
  tracks?: TrackLike[];
};

type RoomLike = {
  name?: string;
  sid?: string;
  metadata?: string;
  numParticipants?: number;
  activeRecording?: boolean;
  creationTime?: unknown;
};

function normalizeEnum(value: unknown): string | undefined {
  if (value === null || value === undefined) {
    return undefined;
  }
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number') {
    return String(value);
  }
  return undefined;
}

function normalizeTimestamp(value: unknown): string | undefined {
  if (!value) {
    return undefined;
  }

  if (value instanceof Date) {
    return value.toISOString();
  }

  if (typeof value === 'string') {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString();
  }

  if (typeof value === 'number') {
    const asMillis = value > 100000000000 ? value : value * 1000;
    const parsed = new Date(asMillis);
    return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
  }

  if (typeof value === 'object') {
    const maybeTimestamp = value as { seconds?: number };
    if (typeof maybeTimestamp.seconds === 'number') {
      const parsed = new Date(maybeTimestamp.seconds * 1000);
      return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
    }
  }

  return undefined;
}

export function toHttpLiveKitUrl(livekitUrl: string): string {
  const parsed = new URL(livekitUrl);
  if (parsed.protocol === 'ws:') {
    parsed.protocol = 'http:';
  } else if (parsed.protocol === 'wss:') {
    parsed.protocol = 'https:';
  }
  return parsed.toString();
}

export function getRoomServiceClient(): RoomServiceClient {
  const config = getServerConfig();
  // Use LIVEKIT_URL_INTERNAL when available (Docker-internal hostname).
  // Falls back through LIVEKIT_URL_PUBLIC → LIVEKIT_URL via getServerConfig().
  const livekitUrl = requireEnv(config.livekitInternalUrl, 'LIVEKIT_URL_INTERNAL');
  const livekitApiKey = requireEnv(config.livekitApiKey, 'LIVEKIT_API_KEY');
  const livekitApiSecret = requireEnv(config.livekitApiSecret, 'LIVEKIT_API_SECRET');
  return new RoomServiceClient(toHttpLiveKitUrl(livekitUrl), livekitApiKey, livekitApiSecret);
}

export function getWebhookReceiver(): WebhookReceiver {
  const config = getServerConfig();
  const livekitApiKey = requireEnv(config.livekitApiKey, 'LIVEKIT_API_KEY');
  const livekitApiSecret = requireEnv(config.livekitApiSecret, 'LIVEKIT_API_SECRET');
  return new WebhookReceiver(livekitApiKey, livekitApiSecret);
}

export function isBotParticipant(participant: { identity: string; name?: string }): boolean {
  return participant.identity.startsWith('bot_') || participant.name?.toLowerCase() === 'assistant';
}

export function mapTrack(track: TrackLike): ConciergeTrack {
  return {
    sid: track.sid ?? '',
    name: track.name,
    source: normalizeEnum(track.source),
    kind: normalizeEnum(track.type),
    muted: track.muted,
    mimeType: track.mimeType,
    width: track.width,
    height: track.height,
  };
}

export function mapParticipant(participant: ParticipantLike): ConciergeParticipant {
  return {
    identity: participant.identity ?? '',
    sid: participant.sid,
    name: participant.name,
    state: normalizeEnum(participant.state),
    metadata: participant.metadata,
    joinedAt: normalizeTimestamp(participant.joinedAt),
    isPublisher: participant.permission?.canPublish,
    tracks: (participant.tracks ?? []).map(mapTrack),
  };
}

export function mapRoom(room: RoomLike): ConciergeRoom {
  return {
    name: room.name ?? '',
    sid: room.sid,
    metadata: room.metadata,
    numParticipants: room.numParticipants,
    activeRecording: room.activeRecording,
    creationTime: normalizeTimestamp(room.creationTime),
  };
}

export function mapWebhookEvent(event: unknown): Omit<ConciergeEvent, 'id'> {
  const eventRecord =
    typeof event === 'object' && event !== null ? (event as Record<string, unknown>) : {};
  const maybeRoom = eventRecord.room as { name?: string } | undefined;
  const maybeParticipant = eventRecord.participant as { identity?: string } | undefined;

  return {
    source: 'webhook',
    event: typeof eventRecord.event === 'string' ? eventRecord.event : 'unknown',
    receivedAt: new Date().toISOString(),
    roomName: maybeRoom?.name,
    participantIdentity: maybeParticipant?.identity,
    payload: eventRecord,
  };
}
