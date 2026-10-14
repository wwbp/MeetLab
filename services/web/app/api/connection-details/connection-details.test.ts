import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NextRequest } from 'next/server';

const mockFetch = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());

vi.stubGlobal('fetch', mockFetch);

vi.mock('@/lib/config/server', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/config/server')>()),
  getServerConfig: mockGetServerConfig,
}));

import { GET } from './route';

const DEFAULT_CONFIG = {
  livekitUrl: 'ws://localhost:7880',
  livekitApiKey: 'devkey',
  livekitApiSecret: 'secret',
  botRunnerUrl: 'http://agent-runner:7860/',
  botRunnerSecret: 'test-secret',
};

/** Each test gets its own client IP so the per-IP rate limiter can't bleed across. */
let ipCounter = 0;
function req(roomName = 'room-1'): NextRequest {
  ipCounter += 1;
  return new NextRequest(
    `http://localhost/api/connection-details?roomName=${roomName}&participantName=abby`,
    { headers: { 'x-forwarded-for': `10.0.0.${ipCounter}` } },
  );
}

const runnerConfig = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('GET /api/connection-details — session limit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(DEFAULT_CONFIG);
    mockFetch.mockResolvedValue(runnerConfig({ session_limit_minutes: 30 }));
  });

  it("hands the browser the room's limit, in seconds", async () => {
    const res = await GET(req('room-with-limit'));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.sessionLimitSeconds).toBe(30 * 60);
    expect(body.participantToken).toBeTruthy();

    // Looked up per room, so a room-scoped config row wins over global.
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe('http://agent-runner:7860/config?room=room-with-limit');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer test-secret');
  });

  it('reports no limit when the room has none', async () => {
    mockFetch.mockResolvedValue(runnerConfig({ session_limit_minutes: 0 }));
    expect((await (await GET(req())).json()).sessionLimitSeconds).toBe(0);
  });

  // The point of these four: a session limit is a nice-to-have, and joining a
  // meeting must never fail or stall because the bot runner is unhappy.
  it('still issues a token when the bot runner is unreachable', async () => {
    mockFetch.mockRejectedValue(new Error('ECONNREFUSED'));
    const body = await (await GET(req())).json();
    expect(body.sessionLimitSeconds).toBe(0);
    expect(body.participantToken).toBeTruthy();
  });

  it('still issues a token when the bot runner errors', async () => {
    mockFetch.mockResolvedValue(runnerConfig({ error: 'boom' }, 500));
    const body = await (await GET(req())).json();
    expect(body.sessionLimitSeconds).toBe(0);
    expect(body.participantToken).toBeTruthy();
  });

  it('still issues a token when the bot runner returns junk', async () => {
    mockFetch.mockResolvedValue(new Response('<html>nope</html>', { status: 200 }));
    const body = await (await GET(req())).json();
    expect(body.sessionLimitSeconds).toBe(0);
    expect(body.participantToken).toBeTruthy();
  });

  it('skips the lookup entirely when no bot runner is configured', async () => {
    mockGetServerConfig.mockReturnValue({ ...DEFAULT_CONFIG, botRunnerUrl: undefined });
    const body = await (await GET(req())).json();
    expect(body.sessionLimitSeconds).toBe(0);
    expect(body.participantToken).toBeTruthy();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('gives the lookup an abort signal so a hung runner cannot stall the join', async () => {
    await GET(req());
    const [, init] = mockFetch.mock.calls[0];
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it('sends no Authorization header when no runner secret is set', async () => {
    mockGetServerConfig.mockReturnValue({ ...DEFAULT_CONFIG, botRunnerSecret: undefined });
    await GET(req());
    const [, init] = mockFetch.mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it('always answers with a response, even on a non-Error throw', async () => {
    // A route handler that falls off the end returns undefined, which Next
    // cannot serve — so the catch must produce a response unconditionally.
    mockGetServerConfig.mockImplementation(() => {
      throw 'not an Error';
    });
    const res = await GET(req());
    expect(res).toBeInstanceOf(Response);
    expect(res.status).toBe(500);
  });

  it('does not look up a limit for a request it rejects', async () => {
    const bad = new NextRequest('http://localhost/api/connection-details?participantName=abby', {
      headers: { 'x-forwarded-for': '10.0.1.1' },
    });
    expect((await GET(bad)).status).toBe(400);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
