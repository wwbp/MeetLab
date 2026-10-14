import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { noteRouteError } from '@/lib/concierge/event-log';

// Prepare for study: warm the bot pool before a study (agent-runner capacity.py).
export const dynamic = 'force-dynamic';

async function runner(path: string, method: string, body?: string): Promise<NextResponse> {
  const { botRunnerUrl, botRunnerSecret } = getServerConfig();
  if (!botRunnerUrl) {
    return NextResponse.json({ error: 'BOT_RUNNER_URL is not configured' }, { status: 500, headers: noStoreHeaders() });
  }
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (botRunnerSecret) headers['Authorization'] = `Bearer ${botRunnerSecret}`;
  try {
    const res = await fetch(`${botRunnerUrl}${path}`, { method, headers, body, cache: 'no-store' });
    return NextResponse.json(await res.json(), { status: res.status, headers: noStoreHeaders() });
  } catch (error) {
    // Not an empty pool: a console showing "0 ready" when the runner is down misleads.
    noteRouteError(`${method} /api/concierge/capacity`, error);
    const message = error instanceof Error ? error.message : 'agent-runner unreachable';
    return NextResponse.json({ error: message }, { status: 502, headers: noStoreHeaders() });
  }
}

export async function GET() {
  return runner('capacity', 'GET');
}

export async function POST(req: NextRequest) {
  return runner('capacity/prewarm', 'POST', await req.text());
}

export async function DELETE() {
  return runner('capacity/prewarm', 'DELETE');
}
