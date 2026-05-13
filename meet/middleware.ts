import { NextRequest, NextResponse } from 'next/server';
import { jwtVerify } from 'jose';

const COOKIE_NAME = 'console-session';

export const config = {
  matcher: ['/', '/config', '/db/:path*', '/api/concierge/:path*', '/api/console/logout', '/api/console/config'],
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
      await jwtVerify(token, secret);
      return NextResponse.next();
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
