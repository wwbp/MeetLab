// The console's Database link reaches SQLAdmin on the runner through meet. In AWS meet forwards
// X-Forwarded-Proto: https, so the runner builds https://agent-runner:7860/... links, and Next
// drops a trailing slash that SQLAdmin's root needs: on production the link led to
// https://agent-runner:7860/api/db/ and every page link with it (2026-10-14).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { proxyToSqlAdmin } from './db-proxy';

const RUNNER = 'http://agent-runner:7860';

function upstream(status: number, headers: Record<string, string>, body = '') {
  const calls: string[] = [];
  vi.stubGlobal('fetch', async (url: string) => {
    calls.push(url);
    return new Response(body || null, { status, headers });
  });
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('proxyToSqlAdmin', () => {
  it('asks the runner for /api/db/ itself, so no redirect can loop with Next dropping the slash', async () => {
    vi.stubEnv('BOT_RUNNER_URL', `${RUNNER}/`);
    const calls = upstream(200, { 'content-type': 'text/plain' }, 'ok');
    await proxyToSqlAdmin(new NextRequest('https://meet.wwbp.org/api/db'));
    expect(calls).toEqual([`${RUNNER}/api/db/`]);
  });

  it("turns the runner's https redirect into a same-site one", async () => {
    vi.stubEnv('BOT_RUNNER_URL', `${RUNNER}/`);
    upstream(302, { location: 'https://agent-runner:7860/api/db/login' });
    const res = await proxyToSqlAdmin(new NextRequest('https://meet.wwbp.org/api/db/conversation/list'));
    expect(res.headers.get('location')).toBe('/api/db/login');
  });

  it("turns every runner link in a page into a same-site one, http or https", async () => {
    vi.stubEnv('BOT_RUNNER_URL', `${RUNNER}/`);
    upstream(200, { 'content-type': 'text/html' },
      '<body><link href="https://agent-runner:7860/api/db/statics/main.css"><form action="http://agent-runner:7860/api/db/login"></body>');
    const html = await (await proxyToSqlAdmin(new NextRequest('https://meet.wwbp.org/api/db/login'))).text();
    expect(html).not.toContain('agent-runner');
    expect(html).toContain('href="/api/db/statics/main.css"');
    expect(html).toContain('action="/api/db/login"');
  });
});
