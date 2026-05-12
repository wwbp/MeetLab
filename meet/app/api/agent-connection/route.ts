import { NextResponse } from 'next/server';
import { AccessToken, type AccessTokenOptions, type VideoGrant } from 'livekit-server-sdk';
import { RoomConfiguration } from '@livekit/protocol';
import { getServerConfig, requireEnv } from '@/lib/config/server';
import type { ConnectionDetails } from '@/lib/types';

export const revalidate = 0;

type ConnectionRequestBody = {
  room_config?: {
    agents?: Array<{ agent_name?: string }>;
  };
  custom_data?: unknown;
};

function createUniqueSuffix(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

async function parseBody(request: Request): Promise<ConnectionRequestBody> {
  const raw = await request.text();
  if (!raw.trim()) {
    return {};
  }
  const parsed = JSON.parse(raw) as unknown;
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('Request body must be a JSON object');
  }
  return parsed as ConnectionRequestBody;
}

function createParticipantToken(
  userInfo: AccessTokenOptions,
  roomName: string,
  agentName: string | undefined,
  apiKey: string,
  apiSecret: string
): Promise<string> {
  const at = new AccessToken(apiKey, apiSecret, {
    ...userInfo,
    ttl: '15m',
  });
  const grant: VideoGrant = {
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canPublishData: true,
    canSubscribe: true,
  };
  at.addGrant(grant);

  if (agentName) {
    at.roomConfig = new RoomConfiguration({
      agents: [{ agentName }],
    });
  }

  return at.toJwt();
}

export async function POST(req: Request) {
  try {
    const config = getServerConfig();
    // Use the public/browser-facing URL for the token so the client can connect.
    const livekitUrl = requireEnv(config.livekitUrl, 'LIVEKIT_URL_PUBLIC');
    const apiKey = requireEnv(config.livekitApiKey, 'LIVEKIT_API_KEY');
    const apiSecret = requireEnv(config.livekitApiSecret, 'LIVEKIT_API_SECRET');

    const body = await parseBody(req);
    const agentName: string | undefined = body?.room_config?.agents?.[0]?.agent_name;

    const participantName = 'user';
    const participantIdentity = `voice_assistant_user_${createUniqueSuffix()}`;
    const roomName = `voice_assistant_room_${createUniqueSuffix()}`;

    const participantToken = await createParticipantToken(
      { identity: participantIdentity, name: participantName },
      roomName,
      agentName,
      apiKey,
      apiSecret
    );

    const botRunnerUrl = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL');
    const apiUrl = botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`;
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      let botResponse: Response;
      try {
        botResponse = await fetch(`${apiUrl}start`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            room_name: roomName,
            room_config: body.room_config,
            custom_data: body.custom_data,
          }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timeout);
      }

      if (!botResponse.ok) {
        const errorText = await botResponse.text();
        return NextResponse.json(
          {
            error: `Failed to start bot (${botResponse.status}): ${errorText || 'bot runner returned an error'}`,
          },
          { status: 502 }
        );
      }

      const responseText = await botResponse.text();
      if (responseText) {
        try {
          const payload = JSON.parse(responseText) as { error?: unknown };
          if (typeof payload.error === 'string' && payload.error.trim()) {
            return NextResponse.json(
              { error: `Failed to start bot: ${payload.error}` },
              { status: 502 }
            );
          }
        } catch {
          // Ignore non-JSON payload on success.
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown bot runner error';
      return NextResponse.json(
        { error: `Failed to contact bot runner: ${message}` },
        { status: 502 }
      );
    }

    const data: ConnectionDetails = {
      serverUrl: livekitUrl,
      roomName,
      participantToken,
      participantName,
    };
    return NextResponse.json(data, {
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    if (error instanceof Error) {
      const status =
        error instanceof SyntaxError || error.message.includes('Request body must be a JSON object')
          ? 400
          : 500;
      return new NextResponse(error.message, { status });
    }
    return new NextResponse('Unexpected error', { status: 500 });
  }
}
