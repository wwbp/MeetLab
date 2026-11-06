import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { noteRouteError } from '@/lib/concierge/event-log';
import { peopleCsv, type MeetingPeople } from '@/lib/people-csv';

export const dynamic = 'force-dynamic';

// One meeting's people with their Prolific IDs, as a CSV download (the console's
// "Download CSV"). Console only (middleware).
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
    if (!res.ok) return NextResponse.json(await res.json(), { status: res.status, headers: noStoreHeaders() });
    const meeting = (await res.json()) as MeetingPeople;
    const filename = `${meeting.room_name.replace(/[^\w.-]/g, '_')}-people.csv`;
    return new NextResponse(peopleCsv(meeting), {
      headers: {
        ...noStoreHeaders(),
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': `attachment; filename="${filename}"`,
      },
    });
  } catch (error) {
    noteRouteError('GET /api/meetings/[id]/people', error);
    return NextResponse.json({ error: 'agent-runner unreachable' }, { status: 502, headers: noStoreHeaders() });
  }
}
