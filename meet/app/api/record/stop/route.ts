import { EgressClient } from 'livekit-server-sdk';
import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';

export async function GET(req: NextRequest) {
  try {
    const roomName = req.nextUrl.searchParams.get('roomName');
    if (!roomName) {
      return new NextResponse('Missing roomName parameter', { status: 400 });
    }

    const config = getServerConfig();
    if (!config.livekitInternalUrl) {
      return new NextResponse('LiveKit server URL is not configured', { status: 500 });
    }

    const hostURL = new URL(config.livekitInternalUrl);
    hostURL.protocol = hostURL.protocol === 'ws:' ? 'http:' : 'https:';

    const egressClient = new EgressClient(
      hostURL.origin,
      config.livekitApiKey,
      config.livekitApiSecret,
    );

    const active = (await egressClient.listEgress({ roomName })).filter((e) => e.status < 2);
    if (active.length === 0) {
      return new NextResponse('No active recording found', { status: 404 });
    }

    await Promise.all(active.map((e) => egressClient.stopEgress(e.egressId)));
    return new NextResponse(null, { status: 200 });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to stop recording';
    return new NextResponse(message, { status: 500 });
  }
}
