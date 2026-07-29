import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { noteRouteError } from '@/lib/concierge/event-log';

export async function GET(req: NextRequest) {
  const roomName = req.nextUrl.searchParams.get('roomName');
  if (!roomName) {
    return new NextResponse('Missing roomName parameter', { status: 400, headers: noStoreHeaders() });
  }

  const { botRunnerUrl, botRunnerSecret } = getServerConfig();
  if (!botRunnerUrl) {
    return new NextResponse('BOT_RUNNER_URL is not configured', { status: 500, headers: noStoreHeaders() });
  }

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (botRunnerSecret) headers['Authorization'] = `Bearer ${botRunnerSecret}`;

  try {
    const res = await fetch(`${botRunnerUrl}recordings/stop`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ room_name: roomName }),
    });

    if (res.status === 404) {
      return new NextResponse('No active recording found', { status: 404, headers: noStoreHeaders() });
    }
    if (!res.ok) {
      const msg = await res.text();
      return new NextResponse(msg, { status: res.status, headers: noStoreHeaders() });
    }
    return new NextResponse(null, { status: 200, headers: noStoreHeaders() });
  } catch (error) {
    noteRouteError('GET /api/record/stop', error);
    const message = error instanceof Error ? error.message : 'Failed to stop recording';
    return new NextResponse(message, { status: 500, headers: noStoreHeaders() });
  }
}
