// The console's start links: create a stored link and list them, through the runner
// (lib/matchmaking-client, mocked here; its shapes are tests/fixtures/matchmaking).
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/matchmaking-client', () => ({ createLink: vi.fn(), listLinks: vi.fn() }));
const client = await import('@/lib/matchmaking-client');
const { GET, POST } = await import('./route');

const post = (body: unknown) =>
  POST(new Request('http://meet.test/api/console/start-link', { method: 'POST', body: JSON.stringify(body) }));

afterEach(() => {
  vi.mocked(client.createLink).mockReset();
  vi.unstubAllEnvs();
});

describe('POST /api/console/start-link', () => {
  it('stores the link with the runner and returns its ID and its /start URL', async () => {
    vi.mocked(client.createLink).mockResolvedValue({ status: 200, body: { link_id: 'Qm9vdHN0cmFwTGlua0lEMQ' } });
    const res = await post({ name: 'Pilot 3', pool: ['friendly'], size: 3, password: 'blue-river' });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      token: 'Qm9vdHN0cmFwTGlua0lEMQ',
      url: 'http://meet.test/start/Qm9vdHN0cmFwTGlua0lEMQ',
    });
    expect(client.createLink).toHaveBeenCalledWith({
      name: 'Pilot 3',
      pool: ['friendly'],
      size: 3,
      min_size: 3,
      delay_s: 0,
      max_wait_s: 300,
      password: 'blue-river',
    });
  });

  it('uses MEET_BASE_URL for the URL when it is set', async () => {
    vi.stubEnv('MEET_BASE_URL', 'https://meet.wwbp.org/');
    vi.mocked(client.createLink).mockResolvedValue({ status: 200, body: { link_id: 'Qm9vdHN0cmFwTGlua0lEMQ' } });
    const res = await post({ pool: ['friendly'] });
    expect((await res.json()).url).toBe('https://meet.wwbp.org/start/Qm9vdHN0cmFwTGlua0lEMQ');
  });

  it('refuses invalid settings without calling the runner', async () => {
    for (const bad of [{ pool: [] }, { name: '', pool: ['friendly'] }, { pool: ['friendly'], size: 9 }]) {
      const res = await post(bad);
      expect(res.status, JSON.stringify(bad)).toBe(400);
      expect((await res.json()).error).toEqual(expect.any(String));
    }
    const res = await POST(new Request('http://meet.test/x', { method: 'POST', body: 'not json' }));
    expect(res.status).toBe(400);
    expect(client.createLink).not.toHaveBeenCalled();
  });

  it("passes the runner's refusal on", async () => {
    vi.mocked(client.createLink).mockResolvedValue({ status: 502, body: { error: 'runner unreachable: x' } });
    const res = await post({ pool: ['friendly'] });
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ error: 'runner unreachable: x' });
  });
});

describe('GET /api/console/start-link', () => {
  it("returns the runner's list as it is", async () => {
    const body = { links: [{ link_id: 'Qm9vdHN0cmFwTGlua0lEMQ', name: 'Pilot 3' }] };
    vi.mocked(client.listLinks).mockResolvedValue({ status: 200, body } as never);
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(body);
  });
});
