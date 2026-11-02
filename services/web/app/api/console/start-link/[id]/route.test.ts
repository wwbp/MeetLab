// Stop / start a stored link (one toggle): the console forwards { active } to the runner.
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/matchmaking-client', () => ({ setLinkActive: vi.fn() }));
const client = await import('@/lib/matchmaking-client');
const { PATCH } = await import('./route');

const patch = (id: string, body: unknown) =>
  PATCH(new Request(`http://meet.test/api/console/start-link/${id}`, { method: 'PATCH', body: JSON.stringify(body) }), {
    params: Promise.resolve({ id }),
  });

describe('PATCH /api/console/start-link/[id]', () => {
  it('stops and starts a link', async () => {
    vi.mocked(client.setLinkActive).mockResolvedValue({
      status: 200,
      body: { link_id: 'Qm9vdHN0cmFwTGlua0lEMQ', active: false },
    });
    const res = await patch('Qm9vdHN0cmFwTGlua0lEMQ', { active: false });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ link_id: 'Qm9vdHN0cmFwTGlua0lEMQ', active: false });
    expect(client.setLinkActive).toHaveBeenCalledWith('Qm9vdHN0cmFwTGlua0lEMQ', false);
  });

  it("passes the runner's 404 on", async () => {
    vi.mocked(client.setLinkActive).mockResolvedValue({ status: 404, body: { error: 'not_found' } });
    const res = await patch('AAAAAAAAAAAAAAAAAAAAAA', { active: true });
    expect(res.status).toBe(404);
  });

  it('refuses a body without a boolean active', async () => {
    vi.mocked(client.setLinkActive).mockClear();
    for (const bad of [{}, { active: 'false' }, null]) {
      expect((await patch('Qm9vdHN0cmFwTGlua0lEMQ', bad)).status, JSON.stringify(bad)).toBe(400);
    }
    expect(client.setLinkActive).not.toHaveBeenCalled();
  });
});
