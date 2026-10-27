import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig, requireEnv } from '@/lib/config/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { noteRouteError } from '@/lib/concierge/event-log';
import { completionCode } from '@/lib/completion-code';
import { normalizeProlificId } from '@/lib/study';

export const dynamic = 'force-dynamic';

// The completion screen's code (docs/study-support.md). Public, since participants are not
// logged in, so the code is the payment: it is only issued to a Prolific ID the runner heard
// speak in one of this room's sessions. Never at join, where anyone could curl it.
export async function GET(request: NextRequest) {
  const roomName = request.nextUrl.searchParams.get('roomName');
  const prolificId = normalizeProlificId(request.nextUrl.searchParams.get('prolificId'));
  if (!roomName || !prolificId) {
    return new NextResponse('roomName and a valid prolificId are required', { status: 400 });
  }
  try {
    const { botRunnerUrl, botRunnerSecret, livekitApiSecret } = getServerConfig();
    const runner = requireEnv(botRunnerUrl, 'BOT_RUNNER_URL');
    const base = runner.endsWith('/') ? runner : `${runner}/`;
    const headers: Record<string, string> = {};
    if (botRunnerSecret) headers['Authorization'] = `Bearer ${botRunnerSecret}`;
    const res = await fetch(`${base}rooms/${encodeURIComponent(roomName)}/speakers`, { headers, cache: 'no-store' });
    if (!res.ok) throw new Error(`agent-runner answered ${res.status}`);
    const { speakers } = (await res.json()) as { speakers: { prolific_id: string | null }[] };
    if (!speakers.some((s) => s.prolific_id === prolificId)) {
      return NextResponse.json({ error: 'not heard in this room' }, { status: 404, headers: noStoreHeaders() });
    }
    const code = completionCode(roomName, prolificId, requireEnv(livekitApiSecret, 'LIVEKIT_API_SECRET'));
    return NextResponse.json({ completionCode: code }, { headers: noStoreHeaders() });
  } catch (error) {
    noteRouteError('GET /api/completion-code', error);
    return NextResponse.json({ error: 'agent-runner unreachable' }, { status: 502, headers: noStoreHeaders() });
  }
}
