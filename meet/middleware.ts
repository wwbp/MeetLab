import { NextRequest, NextResponse } from 'next/server';
import { jwtVerify } from 'jose';

const COOKIE_NAME = 'console-session';

export const config = {
  matcher: [
    '/',
    '/config',
    '/events',
    '/meetings',
    '/start-links',
    '/db/:path*',
    // Session records: metadata, transcripts and recording downloads. Console-only
    // (the only caller is components/desk/meetings-tab.tsx).
    '/api/meetings/:path*',
    '/api/concierge/:path*',
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
