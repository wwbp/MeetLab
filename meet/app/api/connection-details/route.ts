import { randomString } from '@/lib/client-utils';
import { getLiveKitURL } from '@/lib/getLiveKitURL';
import { ConnectionDetails } from '@/lib/types';
import { validateLiveKitPublicUrlForRequestHost } from '@/lib/validateLiveKitPublicUrl';
import { AccessToken, AccessTokenOptions, VideoGrant } from 'livekit-server-sdk';
import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig, requireEnv } from '@/lib/config/server';

const COOKIE_KEY = 'random-participant-postfix';

export async function GET(request: NextRequest) {
  try {
    const config = getServerConfig();
    const livekitUrlPublic = requireEnv(config.livekitUrl, 'LIVEKIT_URL_PUBLIC');
    const apiKey = requireEnv(config.livekitApiKey, 'LIVEKIT_API_KEY');
    const apiSecret = requireEnv(config.livekitApiSecret, 'LIVEKIT_API_SECRET');

    // Parse query parameters
    const roomName = request.nextUrl.searchParams.get('roomName');
    const participantName = request.nextUrl.searchParams.get('participantName');
    const metadata = request.nextUrl.searchParams.get('metadata') ?? '';
    const region = request.nextUrl.searchParams.get('region');

    const incomingHost = request.headers.get('x-forwarded-host') ?? request.headers.get('host');
    const hostValidationError = validateLiveKitPublicUrlForRequestHost(livekitUrlPublic, incomingHost);
    if (hostValidationError) {
      throw new Error(hostValidationError);
    }

    const livekitServerUrl = region ? getLiveKitURL(livekitUrlPublic, region) : livekitUrlPublic;
    if (livekitServerUrl === undefined) {
      throw new Error('Invalid region');
    }

    if (typeof roomName !== 'string') {
      return new NextResponse('Missing required query parameter: roomName', { status: 400 });
    }
    if (participantName === null) {
      return new NextResponse('Missing required query parameter: participantName', { status: 400 });
    }

    let randomParticipantPostfix = request.cookies.get(COOKIE_KEY)?.value;
    if (!randomParticipantPostfix) {
      randomParticipantPostfix = randomString(4);
    }

    const participantToken = await createParticipantToken(
      {
        identity: `${participantName}__${randomParticipantPostfix}`,
        name: participantName,
        metadata,
      },
      roomName,
      apiKey,
      apiSecret,
    );

    const data: ConnectionDetails = {
      serverUrl: livekitServerUrl,
      roomName,
      participantToken,
      participantName,
    };
    return new NextResponse(JSON.stringify(data), {
      headers: {
        'Content-Type': 'application/json',
        'Set-Cookie': `${COOKIE_KEY}=${randomParticipantPostfix}; Path=/; HttpOnly; SameSite=Strict; Secure; Expires=${getCookieExpirationTime()}`,
      },
    });
  } catch (error) {
    if (error instanceof Error) {
      return new NextResponse(error.message, { status: 500 });
    }
  }
}

function createParticipantToken(
  userInfo: AccessTokenOptions,
  roomName: string,
  apiKey: string,
  apiSecret: string,
) {
  const at = new AccessToken(apiKey, apiSecret, userInfo);
  at.ttl = '5m';
  const grant: VideoGrant = {
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canPublishData: true,
    canSubscribe: true,
  };
  at.addGrant(grant);
  return at.toJwt();
}

function getCookieExpirationTime(): string {
  const now = new Date();
  now.setTime(now.getTime() + 60 * 120 * 1000);
  return now.toUTCString();
}
