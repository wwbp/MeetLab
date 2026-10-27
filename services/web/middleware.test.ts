import { describe, it, expect, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { NextRequest } from 'next/server';
import { SignJWT } from 'jose';
import { config, middleware } from './middleware';

/**
 * The console's auth is a path allow-list, which means a new console page or a new
 * admin API is unprotected until someone remembers to add it. That is exactly how
 * `/meetings` and `/api/meetings/**` — session metadata, transcripts and
 * participant audio/video downloads — ended up publicly readable in production
 * (verified live 2026-08-05: `GET /api/meetings` returned all 167 conversations
 * and the audio-tracks download returned a 141 KB zip, both unauthenticated).
 *
 * These tests make the allow-list a derived fact rather than a thing to remember.
 */

/** Does a Next middleware matcher pattern cover this pathname? */
function covers(pattern: string, pathname: string): boolean {
  if (pattern.endsWith('/:path*')) {
    const prefix = pattern.slice(0, -'/:path*'.length);
    return pathname === prefix || pathname.startsWith(`${prefix}/`);
  }
  return pattern === pathname;
}

const isProtected = (pathname: string) => config.matcher.some((p) => covers(p, pathname));

describe('console auth matcher', () => {
  it('protects every page in the console shell', () => {
    const shellDir = path.join(__dirname, 'app', '(shell)');
    const pages = fs
      .readdirSync(shellDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => `/${entry.name}`);

    expect(pages.length).toBeGreaterThan(0);
    for (const page of ['/', ...pages]) {
      expect(isProtected(page), `console page ${page} is not behind auth`).toBe(true);
    }
  });

  it('protects the admin APIs', () => {
    for (const route of [
      '/api/concierge/rooms',
      '/api/concierge/rooms/some-room/bots',
      '/api/concierge/events',
      '/api/meetings',
      '/api/meetings/abc-123/transcript',
      '/api/meetings/abc-123/files/def-456/download',
      '/api/meetings/abc-123/audio-tracks/download',
      '/api/meetings/reconcile',
      '/api/console/config',
      '/api/console/configs',
      '/api/console/start-link',
      '/api/console/logout',
      '/api/db',
      '/api/db/anything',
    ]) {
      expect(isProtected(route), `${route} is not behind auth`).toBe(true);
    }
  });

  it('protects every console API route but login, by enumeration', () => {
    // A hand-written list missed DELETE /api/console/test-data when it was added (2026-10-14):
    // derive the console's admin routes from the directory instead.
    const dir = path.join(__dirname, 'app', 'api', 'console');
    const routes = fs
      .readdirSync(dir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && entry.name !== 'login')
      .map((entry) => `/api/console/${entry.name}`);
    expect(routes.length).toBeGreaterThan(3);
    for (const route of routes) {
      expect(isProtected(route), `${route} is not behind auth`).toBe(true);
    }
  });

  it('leaves the genuinely public surface open', () => {
    // Participants are not logged in, so these must stay reachable.
    for (const route of [
      '/api/health',
      '/api/connection-details',
      '/api/start-link',
      '/api/console/login',
      '/rooms/some-room',
      '/start/some-token',
    ]) {
      expect(isProtected(route), `${route} must remain public`).toBe(false);
    }
  });

  it('documents /api/record as knowingly public', () => {
    // Called from a participant's browser during a call, so console auth would
    // break the in-call record button. It is unauthenticated by necessity, not by
    // oversight — but it currently has NO auth of its own either: `GET
    // /api/record/start?roomName=...` will start an egress for any room name, which
    // also burns the scarce concurrent-egress quota. It needs room-scoped auth.
    expect(isProtected('/api/record/start')).toBe(false);
    expect(isProtected('/api/record/stop')).toBe(false);
  });
});

describe('console session signature', () => {
  // The console session has its own key. LIVEKIT_API_SECRET is held by every bot task and
  // by LiveKit Cloud, so a session it signs must not open the console.
  const CONSOLE = 'console-session-secret-console-session';
  const LIVEKIT = 'livekit-secret-livekit-secret-livekit';
  const sign = (secret: string) =>
    new SignJWT({ sub: 'console' })
      .setProtectedHeader({ alg: 'HS256' })
      .setExpirationTime('1h')
      .sign(new TextEncoder().encode(secret));
  const status = async (cookie: string) =>
    (await middleware(new NextRequest('http://localhost/api/meetings', { headers: { cookie: `console-session=${cookie}` } }))).status;

  afterEach(() => vi.unstubAllEnvs());

  it('accepts a session signed with CONSOLE_SESSION_SECRET', async () => {
    vi.stubEnv('CONSOLE_SESSION_SECRET', CONSOLE);
    vi.stubEnv('LIVEKIT_API_SECRET', LIVEKIT);
    expect(await status(await sign(CONSOLE))).toBe(200);
  });

  it('rejects a session signed with LIVEKIT_API_SECRET', async () => {
    vi.stubEnv('CONSOLE_SESSION_SECRET', CONSOLE);
    vi.stubEnv('LIVEKIT_API_SECRET', LIVEKIT);
    expect(await status(await sign(LIVEKIT))).toBe(401);
  });

  it('rejects every session when CONSOLE_SESSION_SECRET is unset (fails closed)', async () => {
    vi.stubEnv('CONSOLE_SESSION_SECRET', '');
    vi.stubEnv('LIVEKIT_API_SECRET', LIVEKIT);
    expect(await status(await sign(LIVEKIT))).toBe(401);
    expect(await status(await sign(CONSOLE))).toBe(401);
  });
});
