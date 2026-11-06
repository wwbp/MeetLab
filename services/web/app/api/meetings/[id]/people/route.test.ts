import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';

// One meeting's people as a CSV download (the console's "Download CSV"): the runner's
// GET /conversations/{id}/speakers, as a file.
const mockFetch = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());
vi.stubGlobal('fetch', mockFetch);
vi.mock('@/lib/config/server', () => ({ getServerConfig: mockGetServerConfig }));

const call = (id: string) =>
  GET(new NextRequest(`http://localhost/api/meetings/${id}/people`), { params: Promise.resolve({ id }) });

describe('GET /api/meetings/[id]/people', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: 'http://agent-runner:7860/', botRunnerSecret: 's3cret' });
  });

  it('downloads a CSV named after the room', async () => {
    const body = {
      room_name: 'study-1', started_at: '2026-10-27T14:00:00+00:00',
      speakers: [{ display_name: 'Ana', prolific_id: '5f2a91b3c4d5e6f708192a3b', first_spoke_at: '2026-10-27T14:01:00+00:00' }],
    };
    mockFetch.mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));
    const res = await call('conv-1');
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('text/csv');
    expect(res.headers.get('content-disposition')).toBe('attachment; filename="study-1-people.csv"');
    expect(await res.text()).toContain('Ana,5f2a91b3c4d5e6f708192a3b');
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://agent-runner:7860/conversations/conv-1/speakers');
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer s3cret');
  });

  it("passes the runner's 404 on", async () => {
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ error: 'conversation not found' }), { status: 404 }));
    expect((await call('nope')).status).toBe(404);
  });
});
