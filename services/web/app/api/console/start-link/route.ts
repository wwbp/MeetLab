import { NextResponse } from 'next/server';
import { createLink, listLinks } from '@/lib/matchmaking-client';
import { linkSettings } from '@/lib/start-link';

export const dynamic = 'force-dynamic';

function inferMeetBaseUrl(request: Request): string {
  // Same app unless configured: use the request's own origin.
  return (process.env.MEET_BASE_URL || new URL(request.url).origin).replace(/\/$/, '');
}

/** Create a stored start link (the runner keeps it; the URL holds only its ID).
 *  Console-session guarded via middleware. */
export async function POST(request: Request) {
  const settings = linkSettings(await request.json().catch(() => null));
  if ('error' in settings) return NextResponse.json(settings, { status: 400 });
  const { status, body } = await createLink(settings.link);
  if (!('link_id' in body)) return NextResponse.json(body, { status });
  const url = `${inferMeetBaseUrl(request)}/start/${body.link_id}`;
  return NextResponse.json({ token: body.link_id, url });
}

/** Every link, with counts of its tickets by state. */
export async function GET() {
  const { status, body } = await listLinks();
  return NextResponse.json(body, { status });
}
