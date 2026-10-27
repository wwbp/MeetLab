import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

beforeEach(() => {
  delete (globalThis as Record<string, unknown>)['__meetlab_rate_limit__'];
  vi.stubEnv('CONSOLE_PASSWORD', 'right-password');
  vi.stubEnv('LIVEKIT_API_SECRET', 'secret');
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

function login(password: string, ip = '203.0.113.9') {
  return new Request('http://localhost/api/console/login', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-forwarded-for': ip },
    body: JSON.stringify({ password }),
  });
}

describe('POST /api/console/login', () => {
  it('accepts the right password and sets the session cookie', async () => {
    const { POST } = await import('./route');
    const res = await POST(login('right-password'));
    expect(res.status).toBe(200);
    expect(res.headers.get('set-cookie')).toContain('console-session=');
  });

  it('rejects a wrong password, including one of a different length', async () => {
    const { POST } = await import('./route');
    expect((await POST(login('wrong-password'))).status).toBe(401);
    expect((await POST(login('x'))).status).toBe(401);
  });

  it('limits attempts per IP: the 6th in a minute is 429, another IP is unaffected', async () => {
    const { POST } = await import('./route');
    for (let i = 0; i < 5; i++) expect((await POST(login('guess'))).status).toBe(401);
    const blocked = await POST(login('right-password'));
    expect(blocked.status).toBe(429);
    expect(Number(blocked.headers.get('retry-after'))).toBeGreaterThan(0);
    expect((await POST(login('right-password', '198.51.100.1'))).status).toBe(200);
  });
});
