import { NextResponse } from 'next/server';
import { signSession, COOKIE_NAME, SESSION_TTL_SECONDS } from '@/lib/console/auth';

export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  const password = typeof body.password === 'string' ? body.password : '';
  const consolePassword = process.env.CONSOLE_PASSWORD;

  if (!consolePassword || password !== consolePassword) {
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
