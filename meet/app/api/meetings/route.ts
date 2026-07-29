import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { noteRouteError } from '@/lib/concierge/event-log';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const { botRunnerUrl, botRunnerSecret } = getServerConfig();
  if (!botRunnerUrl) {
    return NextResponse.json({ error: 'BOT_RUNNER_URL is not configured' }, { status: 500, headers: noStoreHeaders() });
  }

  const limit = req.nextUrl.searchParams.get('limit') ?? '50';
  const offset = req.nextUrl.searchParams.get('offset') ?? '0';

  const headers: Record<string, string> = {};
  if (botRunnerSecret) headers['Authorization'] = `Bearer ${botRunnerSecret}`;

  try {
    const res = await fetch(
      `${botRunnerUrl}conversations?limit=${limit}&offset=${offset}`,
      { headers, cache: 'no-store' },
    );
    if (!res.ok) {
      const msg = await res.text();
      return NextResponse.json({ error: msg }, { status: res.status, headers: noStoreHeaders() });
    }
    const data = await res.json();
    return NextResponse.json(data, { headers: noStoreHeaders() });
  } catch (error) {
    noteRouteError('GET /api/meetings', error);
    const message = error instanceof Error ? error.message : 'Failed to fetch conversations';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
