import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NextRequest } from 'next/server';
import { GET as startRecording } from './start/route';
import { GET as stopRecording } from './stop/route';

// Both routes now proxy to agent-runner via fetch.
const mockFetch = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());

vi.stubGlobal('fetch', mockFetch);

vi.mock('@/lib/config/server', () => ({
  getServerConfig: mockGetServerConfig,
}));

const DEFAULT_CONFIG = {
  botRunnerUrl: 'http://agent-runner:7860/',
  botRunnerSecret: 'test-secret',
};

function makeOk(status = 200): Response {
  return new Response(null, { status });
}

function makeErr(status: number, body = 'error text'): Response {
  return new Response(body, { status });
}

function startReq(roomName?: string): NextRequest {
  const url = roomName
    ? `http://localhost/api/record/start?roomName=${encodeURIComponent(roomName)}`
    : 'http://localhost/api/record/start';
  return new NextRequest(url);
}

function stopReq(roomName?: string): NextRequest {
  const url = roomName
    ? `http://localhost/api/record/stop?roomName=${encodeURIComponent(roomName)}`
    : 'http://localhost/api/record/stop';
  return new NextRequest(url);
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

  it('returns 400 when roomName is missing', async () => {
    const res = await startRecording(startReq());
    expect(res.status).toBe(400);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('returns 500 when botRunnerUrl is not configured', async () => {
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: undefined, botRunnerSecret: undefined });
    const res = await startRecording(startReq('room-1'));
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
    await startRecording(startReq('room-1'));
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer test-secret');
  });

  it('returns 200 when agent-runner returns 200', async () => {
    mockFetch.mockResolvedValue(makeOk(200));
    const res = await startRecording(startReq('room-1'));
    expect(res.status).toBe(200);
  });

  it('returns 409 when agent-runner returns 409', async () => {
    mockFetch.mockResolvedValue(makeErr(409, 'already recording'));
    const res = await startRecording(startReq('room-1'));
    expect(res.status).toBe(409);
  });

  it('returns 500 when fetch throws', async () => {
    mockFetch.mockRejectedValue(new Error('connection refused'));
    const res = await startRecording(startReq('room-1'));
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

  it('returns 400 when roomName is missing', async () => {
    const res = await stopRecording(stopReq());
    expect(res.status).toBe(400);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('returns 500 when botRunnerUrl is not configured', async () => {
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: undefined, botRunnerSecret: undefined });
    const res = await stopRecording(stopReq('room-1'));
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
    const res = await stopRecording(stopReq('room-1'));
    expect(res.status).toBe(200);
  });

  it('returns 404 when agent-runner returns 404', async () => {
    mockFetch.mockResolvedValue(makeErr(404, 'no active recording'));
    const res = await stopRecording(stopReq('room-1'));
    expect(res.status).toBe(404);
  });

  it('returns 500 when fetch throws', async () => {
    mockFetch.mockRejectedValue(new Error('network error'));
    const res = await stopRecording(stopReq('room-1'));
    expect(res.status).toBe(500);
  });
});
