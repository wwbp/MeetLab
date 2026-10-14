import { NextResponse } from 'next/server';
import { signStartLink } from '@/lib/start-link';

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

/** Generate a shareable meeting start link for a pool of bot-config scopes.
 *  Console-session guarded via middleware. */
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  const pool: unknown = body?.pool;

  if (
    !Array.isArray(pool) ||
    pool.length === 0 ||
    !pool.every((s) => typeof s === 'string' && s.trim().length > 0)
  ) {
    return NextResponse.json(
      { error: 'pool must be a non-empty array of config scope names' },
      { status: 400 }
    );
  }

  const token = await signStartLink(pool);
  const url = `${inferMeetBaseUrl(request)}/start/${encodeURIComponent(token)}`;
  return NextResponse.json({ token, url });
}
