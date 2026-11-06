import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NextRequest } from 'next/server';
import { completionCode } from '@/lib/completion-code';

const mockFetch = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());

vi.stubGlobal('fetch', mockFetch);

vi.mock('@/lib/config/server', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/config/server')>()),
  getServerConfig: mockGetServerConfig,
}));

import { GET } from './route';

const PID = '5f2a91b3c4d5e6f708192a3b';
const CONFIG = {
  livekitApiSecret: 'secret',
  botRunnerUrl: 'http://agent-runner:7860/',
  botRunnerSecret: 'test-secret',
};

const ask = (roomName: string, prolificId: string) =>
  GET(
    new NextRequest(
      `http://localhost/api/completion-code?roomName=${roomName}&prolificId=${prolificId}`,
    ),
  );
const speakers = (list: unknown[]) =>
  new Response(JSON.stringify({ speakers: list }), { status: 200 });

describe('GET /api/completion-code', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(CONFIG);
  });

  it("gives the code to someone who spoke in the room, keyed on their Prolific ID", async () => {
    mockFetch.mockResolvedValue(speakers([{ speaker: 'Ana__x1y2', prolific_id: PID }]));
    const res = await ask('study-7', PID.toUpperCase());
    expect(res.status).toBe(200);
    expect((await res.json()).completionCode).toBe(completionCode('study-7', PID, 'secret'));

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe('http://agent-runner:7860/rooms/study-7/speakers');
    expect(init.headers.Authorization).toBe('Bearer test-secret');
  });

  it('refuses someone the room never heard from (the code is payment)', async () => {
    mockFetch.mockResolvedValue(speakers([{ speaker: 'Ben__a1b2', prolific_id: 'a1b2c3d4e5f60718293a4b5c' }]));
    const res = await ask('study-7', PID);
    expect(res.status).toBe(404);
    expect(await res.text()).not.toContain(completionCode('study-7', PID, 'secret'));
  });

  it('refuses a missing or malformed Prolific ID without asking the runner', async () => {
    expect((await ask('study-7', 'nope')).status).toBe(400);
    expect((await GET(new NextRequest(`http://localhost/api/completion-code?prolificId=${PID}`))).status).toBe(400);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('gives no code when the runner cannot vouch for them', async () => {
    mockFetch.mockRejectedValue(new Error('ECONNREFUSED'));
    expect((await ask('study-7', PID)).status).toBe(502);
    mockFetch.mockResolvedValue(new Response('boom', { status: 500 }));
    expect((await ask('study-7', PID)).status).toBe(502);
  });
});
