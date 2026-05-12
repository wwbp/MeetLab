import { NextResponse } from 'next/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';

export const dynamic = 'force-dynamic';

function trimTrailingSlash(value: string): string {
  return value.endsWith('/') ? value.slice(0, -1) : value;
}

function inferMeetBaseUrl(request: Request): string {
  const configured = process.env.MEET_BASE_URL;
  if (configured) {
    return trimTrailingSlash(configured);
  }
  // Same app — use the request's own origin.
  return new URL(request.url).origin;
}

export async function GET(request: Request, context: { params: Promise<{ roomName: string }> }) {
  const params = await context.params;
  const roomName = params.roomName.trim();
  if (!roomName) {
    return NextResponse.json(
      { error: 'Room name is required' },
      { status: 400, headers: noStoreHeaders() }
    );
  }

  const meetBaseUrl = inferMeetBaseUrl(request);
  const meetJoinUrl = `${meetBaseUrl}/rooms/${encodeURIComponent(roomName)}`;
  const shareText = `Join room "${roomName}" in LiveKit Meet: ${meetJoinUrl}`;

  return NextResponse.json(
    {
      invite: {
        roomName,
        meetJoinUrl,
        shareText,
      },
    },
    { headers: noStoreHeaders() }
  );
}
