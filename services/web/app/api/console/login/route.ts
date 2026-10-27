import { timingSafeEqual } from 'crypto';
import { NextResponse } from 'next/server';
import { signSession, COOKIE_NAME, SESSION_TTL_SECONDS } from '@/lib/console/auth';
import { checkRateLimit, getClientIp } from '@/lib/rate-limit';

// Password guessing: 5 attempts per minute per IP.
const RATE_LIMIT_MAX = 5;
const RATE_LIMIT_WINDOW_MS = 60_000;

function passwordMatches(given: string, expected: string): boolean {
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function POST(request: Request) {
  const limit = checkRateLimit(`console-login:${getClientIp(request)}`, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW_MS);
  if (!limit.allowed) {
    return NextResponse.json(
      { error: 'Too many attempts — try again shortly' },
      { status: 429, headers: { 'Retry-After': String(Math.ceil(limit.retryAfterMs / 1000)) } }
    );
  }

  const body = await request.json().catch(() => ({}));
  const password = typeof body.password === 'string' ? body.password : '';
  const consolePassword = process.env.CONSOLE_PASSWORD;

  if (!consolePassword || !passwordMatches(password, consolePassword)) {
    return NextResponse.json({ error: 'Invalid password' }, { status: 401 });
  }

  const token = await signSession();
  const response = NextResponse.json({ ok: true });
  response.cookies.set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'strict',
    path: '/',
    maxAge: SESSION_TTL_SECONDS,
  });
  return response;
}
