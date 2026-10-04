import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest';
import { NextRequest } from 'next/server';
import { GET as startRecording } from './start/route';
import { GET as stopRecording } from './stop/route';
import { SignJWT } from 'jose';

// Both routes now proxy to agent-runner via fetch.
const mockFetch = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());

vi.stubGlobal('fetch', mockFetch);

vi.mock('@/lib/config/server', () => ({
  getServerConfig: mockGetServerConfig,
}));

const LIVEKIT_SECRET = 'livekit-secret-livekit-secret-1234';
const DEFAULT_CONFIG = {
  botRunnerUrl: 'http://agent-runner:7860/',
  botRunnerSecret: 'test-secret',
  livekitApiSecret: LIVEKIT_SECRET,
};

// F10: only a logged-in console session may record (lib/record-auth.ts).
let consoleSession = '';
let roomToken = '';
beforeAll(async () => {
  const key = new TextEncoder().encode(LIVEKIT_SECRET);
  const exp = Math.floor(Date.now() / 1000) + 300;
  consoleSession = await new SignJWT({ sub: 'console' }).setProtectedHeader({ alg: 'HS256' }).setExpirationTime(exp).sign(key);
  roomToken = await new SignJWT({ video: { room: 'my-room', roomJoin: true } }).setProtectedHeader({ alg: 'HS256' }).setExpirationTime(exp).sign(key);
});

const as = (who: 'console' | 'participant' | 'nobody') =>
  who === 'console' ? { headers: { cookie: `console-session=${consoleSession}` } }
    : who === 'participant' ? { headers: { authorization: `Bearer ${roomToken}` } } : {};

function makeOk(status = 200): Response {
  return new Response(null, { status });
}

function makeErr(status: number, body = 'error text'): Response {
  return new Response(body, { status });
}

function startReq(roomName?: string, who: 'console' | 'participant' | 'nobody' = 'console'): NextRequest {
  const url = roomName
    ? `http://localhost/api/record/start?roomName=${encodeURIComponent(roomName)}`
    : 'http://localhost/api/record/start';
  return new NextRequest(url, as(who));
}

function stopReq(roomName?: string, who: 'console' | 'participant' | 'nobody' = 'console'): NextRequest {
  const url = roomName
    ? `http://localhost/api/record/stop?roomName=${encodeURIComponent(roomName)}`
    : 'http://localhost/api/record/stop';
  return new NextRequest(url, as(who));
}

// ---------------------------------------------------------------------------
// /api/record/start
// ---------------------------------------------------------------------------
describe('GET /api/record/start', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(DEFAULT_CONFIG);
    mockFetch.mockResolvedValue(makeOk(200));
  });

  it('returns 401 to anyone but a console session, a participant of the room included (F10)', async () => {
    expect((await startRecording(startReq('my-room', 'nobody'))).status).toBe(401);
    expect((await startRecording(startReq('my-room', 'participant'))).status).toBe(401);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('returns 400 when roomName is missing', async () => {
    const res = await startRecording(startReq());
    expect(res.status).toBe(400);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('returns 500 when botRunnerUrl is not configured', async () => {
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: undefined, botRunnerSecret: undefined, livekitApiSecret: LIVEKIT_SECRET });
    const res = await startRecording(startReq('my-room'));
    expect(res.status).toBe(500);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('proxies POST to agent-runner recordings/start with room_name', async () => {
    await startRecording(startReq('my-room'));
    expect(mockFetch).toHaveBeenCalledWith(
      'http://agent-runner:7860/recordings/start',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ room_name: 'my-room' }),
      }),
    );
  });

  it('sends Authorization header when botRunnerSecret is set', async () => {
    await startRecording(startReq('my-room'));
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer test-secret');
  });

  it('returns 200 when agent-runner returns 200', async () => {
    mockFetch.mockResolvedValue(makeOk(200));
    const res = await startRecording(startReq('my-room'));
    expect(res.status).toBe(200);
  });

  it('returns 409 when agent-runner returns 409', async () => {
    mockFetch.mockResolvedValue(makeErr(409, 'already recording'));
    const res = await startRecording(startReq('my-room'));
    expect(res.status).toBe(409);
  });

  it('returns 500 when fetch throws', async () => {
    mockFetch.mockRejectedValue(new Error('connection refused'));
    const res = await startRecording(startReq('my-room'));
    expect(res.status).toBe(500);
  });
});

// ---------------------------------------------------------------------------
// /api/record/stop
// ---------------------------------------------------------------------------
describe('GET /api/record/stop', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(DEFAULT_CONFIG);
    mockFetch.mockResolvedValue(makeOk(200));
  });

  it('returns 401 to anyone but a console session, a participant of the room included (F10)', async () => {
    expect((await stopRecording(stopReq('my-room', 'nobody'))).status).toBe(401);
    expect((await stopRecording(stopReq('my-room', 'participant'))).status).toBe(401);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('returns 400 when roomName is missing', async () => {
    const res = await stopRecording(stopReq());
    expect(res.status).toBe(400);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('returns 500 when botRunnerUrl is not configured', async () => {
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: undefined, botRunnerSecret: undefined, livekitApiSecret: LIVEKIT_SECRET });
    const res = await stopRecording(stopReq('my-room'));
    expect(res.status).toBe(500);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('proxies POST to agent-runner recordings/stop with room_name', async () => {
    await stopRecording(stopReq('my-room'));
    expect(mockFetch).toHaveBeenCalledWith(
      'http://agent-runner:7860/recordings/stop',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ room_name: 'my-room' }),
      }),
    );
  });

  it('returns 200 when agent-runner returns 200', async () => {
    const res = await stopRecording(stopReq('my-room'));
    expect(res.status).toBe(200);
  });

  it('returns 404 when agent-runner returns 404', async () => {
    mockFetch.mockResolvedValue(makeErr(404, 'no active recording'));
    const res = await stopRecording(stopReq('my-room'));
    expect(res.status).toBe(404);
  });

  it('returns 500 when fetch throws', async () => {
    mockFetch.mockRejectedValue(new Error('network error'));
    const res = await stopRecording(stopReq('my-room'));
    expect(res.status).toBe(500);
  });
});
