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

const PID = '5f8e2a1b3c4d5e6f7a8b9c0d';

const ready = (ticket: unknown, participant_id?: unknown) =>
  POST(
    new Request('http://localhost/api/start-link/ready', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket, participant_id }),
    }),
  );

describe('POST /api/start-link/ready', () => {
  beforeEach(() => vi.clearAllMocks());

  it('passes on a Prolific ID, cleaned up', async () => {
    mockReady.mockResolvedValue(reply('tickets_ready'));
    expect((await ready(TICKET, ` ${PID.toUpperCase()} `)).status).toBe(200);
    expect(mockReady).toHaveBeenCalledWith(TICKET, PID);
  });

  it('an ID that is not a Prolific ID never reaches the runner', async () => {
    for (const bad of ['not-an-id', 42, '']) {
      expect((await ready(TICKET, bad)).status, String(bad)).toBe(400);
    }
    expect(mockReady).not.toHaveBeenCalled();
  });

  it("passes on the runner's refusal of an ID in use", async () => {
    mockReady.mockResolvedValue({ status: 409, body: { error: 'id_in_use' } });
    const res = await ready(TICKET, PID);
    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({ error: 'id_in_use' });
  });

  it('presses Ready for the ticket and returns its view', async () => {
    mockReady.mockResolvedValue(reply('tickets_ready'));
    const res = await ready(TICKET);
    expect(res.status).toBe(200);
    expect(mockReady).toHaveBeenCalledWith(TICKET, undefined);
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
