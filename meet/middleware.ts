import { NextRequest, NextResponse } from 'next/server';
import { jwtVerify } from 'jose';

const COOKIE_NAME = 'console-session';

// This matcher is an ALLOW-LIST OF PROTECTED PATHS: anything not listed here is
// public. A new console page or admin API is therefore world-readable until
// someone remembers to add it. `middleware.test.ts` enumerates app/(shell) and
// app/api and fails when something is missing — keep both in sync.
//
// 2026-08-05: `/meetings` and `/api/meetings/*` were missing and were serving
// meeting metadata, transcripts and participant audio/video downloads
// unauthenticated in production. See docs/distillation-audit.md (Iteration 3).
export const config = {
  matcher: [
    '/',
    '/config',
    '/meetings',
    '/start-links',
    '/db/:path*',
    '/api/concierge/:path*',
    // Console-only meeting data: listing, transcripts, and the audio/recording
    // download proxies. Only the console calls these, and it carries a session
    // cookie, so protecting them breaks no legitimate caller. Both forms are
    // listed rather than relying on `:path*` also matching the bare path.
    '/api/meetings',
    '/api/meetings/:path*',
    '/api/console/logout',
    '/api/console/config',
    '/api/console/configs',
    '/api/console/start-link',
  ],
};

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Webhook uses its own LiveKit JWT auth — not console session
  if (pathname === '/api/concierge/webhooks/livekit') {
    return NextResponse.next();
  }

  const token = request.cookies.get(COOKIE_NAME)?.value;
  if (token) {
    try {
      const secret = new TextEncoder().encode(process.env.LIVEKIT_API_SECRET ?? '');
      const { payload } = await jwtVerify(token, secret);
      // Only a real console session may pass. Other JWTs signed with the same secret
      // exist (LiveKit participant tokens, public start-link tokens) — without this
      // claim check any of them could be pasted in as a console-session cookie.
      if (payload.sub === 'console') {
        return NextResponse.next();
      }
    } catch {
      // invalid or expired token — fall through
    }
  }

  if (pathname.startsWith('/api/')) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const loginUrl = new URL('/login', request.url);
  loginUrl.searchParams.set('from', pathname);
  return NextResponse.redirect(loginUrl);
}
