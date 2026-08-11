import { randomString } from '@/lib/client-utils';
import { getLiveKitURL } from '@/lib/getLiveKitURL';
import { checkRateLimit, getClientIp } from '@/lib/rate-limit';
import { sessionLimitSecondsFromConfig } from '@/lib/session-limit';
import { ConnectionDetails } from '@/lib/types';
import { validateLiveKitPublicUrlForRequestHost } from '@/lib/validateLiveKitPublicUrl';
import { AccessToken, AccessTokenOptions, VideoGrant } from 'livekit-server-sdk';
import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig, requireEnv } from '@/lib/config/server';
import { noteRouteError } from '@/lib/concierge/event-log';

const COOKIE_KEY = 'random-participant-postfix';

// The bot runner is not on the critical path for joining — if the config lookup
// is slow or fails, the participant joins with no session limit rather than not
// at all. Kept short for the same reason.
const SESSION_LIMIT_LOOKUP_TIMEOUT_MS = 2_000;

// 20 token requests per minute per IP — sufficient for legitimate users,
// blocks automated token farming.
const RATE_LIMIT_MAX = 20;
const RATE_LIMIT_WINDOW_MS = 60_000;

/**
 * Validates that the request's Origin header, when present, matches the
 * server host. Browsers always set Origin on cross-origin fetches; its
 * absence on a same-origin fetch is normal and is allowed through.
 * Mismatches indicate a cross-site or tool-driven request.
 */
function isOriginAllowed(request: NextRequest): boolean {
  const origin = request.headers.get('origin');
  if (!origin) return true; // absent = same-origin browser fetch or server-side call

  try {
    const originHost = new URL(origin).host;
    const requestHost =
      request.headers.get('x-forwarded-host') ?? request.headers.get('host') ?? '';
    return originHost === requestHost;
  } catch {
    return false;
  }
}

export async function GET(request: NextRequest) {
  try {
    // --- Security: origin check ---
    if (!isOriginAllowed(request)) {
      return new NextResponse('Forbidden', { status: 403 });
    }

    // --- Security: rate limit by IP ---
    const ip = getClientIp(request);
    const { allowed, retryAfterMs } = checkRateLimit(
      `connection-details:${ip}`,
      RATE_LIMIT_MAX,
      RATE_LIMIT_WINDOW_MS,
    );
    if (!allowed) {
      return new NextResponse('Too Many Requests', {
        status: 429,
        headers: {
          'Retry-After': String(Math.ceil(retryAfterMs / 1000)),
        },
      });
    }

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

    const [participantToken, sessionLimitSeconds] = await Promise.all([
      createParticipantToken(
        {
          identity: `${participantName}__${randomParticipantPostfix}`,
          name: participantName,
          metadata,
        },
        roomName,
        apiKey,
        apiSecret,
      ),
      fetchSessionLimitSeconds(roomName),
    ]);

    const data: ConnectionDetails = {
      serverUrl: livekitServerUrl,
      roomName,
      participantToken,
      participantName,
      sessionLimitSeconds,
    };
    return new NextResponse(JSON.stringify(data), {
      headers: {
        'Content-Type': 'application/json',
        'Set-Cookie': `${COOKIE_KEY}=${randomParticipantPostfix}; Path=/; HttpOnly; SameSite=Strict; Secure; Expires=${getCookieExpirationTime()}`,
      },
    });
  } catch (error) {
    noteRouteError('GET /api/connection-details', error);
    // Unconditional: falling off the end of a route handler returns undefined,
    // which Next cannot serve.
    const message = error instanceof Error ? error.message : 'Internal Server Error';
    return new NextResponse(message, { status: 500 });
  }
}

/**
 * Ask the bot runner for this room's session limit. Never throws: any failure
 * (runner down, no BOT_RUNNER_URL, timeout, junk payload) means "no limit".
 */
async function fetchSessionLimitSeconds(roomName: string): Promise<number> {
  const { botRunnerUrl, botRunnerSecret } = getServerConfig();
  if (!botRunnerUrl) return 0;

  const base = botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`;
  const headers: HeadersInit = botRunnerSecret
    ? { Authorization: `Bearer ${botRunnerSecret}` }
    : {};

  try {
    const res = await fetch(`${base}config?room=${encodeURIComponent(roomName)}`, {
      headers,
      cache: 'no-store',
      signal: AbortSignal.timeout(SESSION_LIMIT_LOOKUP_TIMEOUT_MS),
    });
    if (!res.ok) return 0;
    return sessionLimitSecondsFromConfig(await res.json());
  } catch {
    return 0;
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
