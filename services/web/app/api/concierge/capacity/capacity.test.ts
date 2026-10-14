import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NextRequest } from 'next/server';
import { GET, POST, DELETE } from './route';

// Prepare for study: the console proxies to agent-runner's /capacity (capacity.py).
const mockFetch = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());
vi.stubGlobal('fetch', mockFetch);
vi.mock('@/lib/config/server', () => ({ getServerConfig: mockGetServerConfig }));

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('/api/concierge/capacity', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: 'http://agent-runner:7860/', botRunnerSecret: 's3cret' });
  });

  it('GET passes the runner status through, with the runner secret', async () => {
    mockFetch.mockResolvedValue(json({ available: true, ready_instances: 1 }));
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ available: true, ready_instances: 1 });
    expect(mockFetch).toHaveBeenCalledWith('http://agent-runner:7860/capacity',
      expect.objectContaining({ method: 'GET', headers: expect.objectContaining({ Authorization: 'Bearer s3cret' }) }));
  });

  it('POST forwards sessions and end time, and the runner refusal with its status', async () => {
    mockFetch.mockResolvedValue(json({ error: 'bad request: the end time must be in the future' }, 400));
    const body = { sessions: 4, until: '2026-10-02T15:00:00+00:00' };
    const res = await POST(new NextRequest('http://localhost/api/concierge/capacity', { method: 'POST', body: JSON.stringify(body) }));
    expect(res.status).toBe(400);
    expect((await res.json()).error).toContain('future');
    expect(mockFetch).toHaveBeenCalledWith('http://agent-runner:7860/capacity/prewarm',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(body) }));
  });

  it('DELETE cancels the warm pool', async () => {
    mockFetch.mockResolvedValue(json({ available: true, min_instances: 0 }));
    const res = await DELETE();
    expect(res.status).toBe(200);
    expect(mockFetch).toHaveBeenCalledWith('http://agent-runner:7860/capacity/prewarm', expect.objectContaining({ method: 'DELETE' }));
  });

  it('says so when the runner is unreachable instead of showing an empty pool', async () => {
    mockFetch.mockRejectedValue(new Error('connect ECONNREFUSED'));
    const res = await GET();
    expect(res.status).toBe(502);
    expect((await res.json()).error).toContain('ECONNREFUSED');
  });
});
