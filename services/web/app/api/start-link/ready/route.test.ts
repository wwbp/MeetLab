import fs from 'node:fs';
import path from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockReady = vi.hoisted(() => vi.fn());
vi.mock('@/lib/matchmaking-client', () => ({ readyTicket: mockReady }));

import { POST } from './route';

const reply = (name: string) =>
  JSON.parse(
    fs.readFileSync(
      path.join(__dirname, '../../../../../../tests/fixtures/matchmaking', `${name}.json`),
      'utf8',
    ),
  ).response;

const TICKET = '6f1c2b3a-4d5e-4f60-8a7b-9c0d1e2f3a4b';

const ready = (ticket: unknown) =>
  POST(
    new Request('http://localhost/api/start-link/ready', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket }),
    }),
  );

describe('POST /api/start-link/ready', () => {
  beforeEach(() => vi.clearAllMocks());

  it('presses Ready for the ticket and returns its view', async () => {
    mockReady.mockResolvedValue(reply('tickets_ready'));
    const res = await ready(TICKET);
    expect(res.status).toBe(200);
    expect(mockReady).toHaveBeenCalledWith(TICKET);
    expect((await res.json()).view).toEqual({
      kind: 'ready_check',
      deadline_in_ms: 12000,
      pressed: true,
    });
  });

  it('unknown ticket: 404; not a token: 400', async () => {
    mockReady.mockResolvedValue(reply('tickets_ready_unknown'));
    expect((await ready('00000000-0000-4000-8000-000000000000')).status).toBe(404);
    expect((await ready({ id: 1 })).status).toBe(400);
  });
});
