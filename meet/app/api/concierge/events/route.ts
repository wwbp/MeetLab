import { NextResponse } from 'next/server';
import { listRunnerEvents } from '@/lib/concierge/event-log';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import type { EventSeverity } from '@/lib/concierge/types';

export const dynamic = 'force-dynamic';

const SEVERITIES: EventSeverity[] = ['info', 'warning', 'error'];
const DEFAULT_LIMIT = 100;
const MAX_LIMIT = 500;

function parseLimit(raw: string | null): number {
  const parsed = raw ? Number.parseInt(raw, 10) : Number.NaN;
  if (!Number.isFinite(parsed)) return DEFAULT_LIMIT;
  return Math.min(MAX_LIMIT, Math.max(1, parsed));
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const severityParam = searchParams.get('severity');
  const room = searchParams.get('room') ?? undefined;

  if (severityParam && !SEVERITIES.includes(severityParam as EventSeverity)) {
    return NextResponse.json(
      { error: `severity must be one of: ${SEVERITIES.join(', ')}` },
      { status: 400, headers: noStoreHeaders() },
    );
  }

  try {
    const events = await listRunnerEvents({
      severity: (severityParam as EventSeverity) ?? undefined,
      room,
      limit: parseLimit(searchParams.get('limit')),
    });
    return NextResponse.json({ events }, { headers: noStoreHeaders() });
  } catch (error) {
    // Deliberately not an empty list: a console that renders "no events" when
    // the log is unreachable reads as "nothing went wrong".
    const message = error instanceof Error ? error.message : 'Event log unavailable';
    return NextResponse.json({ error: message }, { status: 502, headers: noStoreHeaders() });
  }
}
