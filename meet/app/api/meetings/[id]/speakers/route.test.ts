import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';

// Who took part in a conversation, with their Prolific IDs (the runner's GET /conversations/{id}/speakers):
// the payment export, and the live study-flow test.
const mockFetch = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());
vi.stubGlobal('fetch', mockFetch);
vi.mock('@/lib/config/server', () => ({ getServerConfig: mockGetServerConfig }));

const call = (id: string) =>
  GET(new NextRequest(`http://localhost/api/meetings/${id}/speakers`), { params: Promise.resolve({ id }) });

describe('GET /api/meetings/[id]/speakers', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: 'http://agent-runner:7860/', botRunnerSecret: 's3cret' });
  });

  it("returns the runner's people, asking with the runner key", async () => {
    const body = { speakers: [{ speaker: 'Ana__x', display_name: 'Ana', prolific_id: '5f2a91b3c4d5e6f708192a3b' }] };
    mockFetch.mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));
    const res = await call('conv-1');
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(body);
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://agent-runner:7860/conversations/conv-1/speakers');
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer s3cret');
  });

  it("passes the runner's 404 on", async () => {
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ error: 'conversation not found' }), { status: 404 }));
    expect((await call('nope')).status).toBe(404);
  });

  it('escapes the id', async () => {
    mockFetch.mockResolvedValue(new Response('{"speakers":[]}', { status: 200 }));
    await call('a/b');
    expect(mockFetch.mock.calls[0][0]).toBe('http://agent-runner:7860/conversations/a%2Fb/speakers');
  });
});
