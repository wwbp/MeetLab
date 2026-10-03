import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';

// A conversation's turns as data (the runner's GET /conversations/{id}/utterances), for the
// console and the load test's quality scores (agent-runner/quality.py).
const mockFetch = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());
vi.stubGlobal('fetch', mockFetch);
vi.mock('@/lib/config/server', () => ({ getServerConfig: mockGetServerConfig }));

const call = (id: string) =>
  GET(new NextRequest(`http://localhost/api/meetings/${id}/utterances`), { params: Promise.resolve({ id }) });

describe('GET /api/meetings/[id]/utterances', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: 'http://agent-runner:7860/', botRunnerSecret: 's3cret' });
  });

  it("returns the runner's turns, asking with the runner key", async () => {
    const body = { utterances: [{ speaker: 'bot_x', bot: true, ts: 1000.5, text: 'Hello.' }] };
    mockFetch.mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));
    const res = await call('conv-1');
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(body);
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://agent-runner:7860/conversations/conv-1/utterances');
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer s3cret');
  });

  it("passes the runner's 404 on", async () => {
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ error: 'conversation not found' }), { status: 404 }));
    expect((await call('nope')).status).toBe(404);
  });

  it('escapes the id', async () => {
    mockFetch.mockResolvedValue(new Response('{"utterances":[]}', { status: 200 }));
    await call('a/b');
    expect(mockFetch.mock.calls[0][0]).toBe('http://agent-runner:7860/conversations/a%2Fb/utterances');
  });
});
