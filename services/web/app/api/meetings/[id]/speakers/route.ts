import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { noteRouteError } from '@/lib/concierge/event-log';

export const dynamic = 'force-dynamic';

// Who took part in a conversation, in the order they first spoke, with the Prolific ID a
// paid study is matched and paid on (docs/study-support.md). Console only (middleware).
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { botRunnerUrl, botRunnerSecret } = getServerConfig();
  if (!botRunnerUrl) {
    return NextResponse.json({ error: 'BOT_RUNNER_URL is not configured' }, { status: 500, headers: noStoreHeaders() });
  }
  const base = botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`;
  const headers: Record<string, string> = {};
  if (botRunnerSecret) headers['Authorization'] = `Bearer ${botRunnerSecret}`;
  try {
    const res = await fetch(`${base}conversations/${encodeURIComponent(id)}/speakers`, { headers, cache: 'no-store' });
    return NextResponse.json(await res.json(), { status: res.status, headers: noStoreHeaders() });
  } catch (error) {
    noteRouteError('GET /api/meetings/[id]/speakers', error);
    return NextResponse.json({ error: 'agent-runner unreachable' }, { status: 502, headers: noStoreHeaders() });
  }
}
