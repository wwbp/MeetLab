import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getRoomSession } from './bot-runner';

// Iteration 9: which bot a room has comes from the runner's session row, not meet's memory.
const mockFetch = vi.hoisted(() => vi.fn());
vi.stubGlobal('fetch', mockFetch);
vi.mock('@/lib/config/server', () => ({
  getServerConfig: () => ({ botRunnerUrl: 'http://agent-runner:7860/', botRunnerSecret: 's3cret' }),
  requireEnv: (v: string) => v,
}));

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe('getRoomSession', () => {
  beforeEach(() => vi.clearAllMocks());

  it("returns the room's running session", async () => {
    const session = { session_id: 's1', bot_identity: 'bot_r_1', started_at: '2026-10-02T12:00:00+00:00' };
    mockFetch.mockResolvedValue(json({ session }));
    expect(await getRoomSession('room a')).toEqual(session);
    expect(mockFetch).toHaveBeenCalledWith('http://agent-runner:7860/rooms/room%20a/session',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer s3cret' }) }));
  });

  it('returns null when the room has no bot', async () => {
    mockFetch.mockResolvedValue(json({ session: null }));
    expect(await getRoomSession('r')).toBeNull();
  });

  it('throws when the runner cannot answer, so the console never shows "no bot" by mistake', async () => {
    mockFetch.mockResolvedValue(json({ error: 'db down' }, 500));
    await expect(getRoomSession('r')).rejects.toThrow('500');
  });
});
