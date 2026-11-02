import fs from 'node:fs';
import path from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { completionCode } from '@/lib/completion-code';

const mockPoll = vi.hoisted(() => vi.fn());
vi.mock('@/lib/matchmaking-client', () => ({ pollTicket: mockPoll }));

import { POST } from './route';

// The runner's replies, from the shared contract (tests/fixtures/matchmaking).
const reply = (name: string) =>
  JSON.parse(
    fs.readFileSync(
      path.join(__dirname, '../../../../../../tests/fixtures/matchmaking', `${name}.json`),
      'utf8',
    ),
  ).response;

const TICKET = '6f1c2b3a-4d5e-4f60-8a7b-9c0d1e2f3a4b';
const PID = '5f8e2a1b3c4d5e6f7a8b9c0d';
const LINK = 'Qm9vdHN0cmFwTGlua0lEMQ';

const status = (ticket: unknown, ip = '10.0.0.1') =>
  POST(
    new Request('http://localhost/api/start-link/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-forwarded-for': ip },
      body: JSON.stringify({ ticket }),
    }),
  );

describe('POST /api/start-link/status', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    vi.stubEnv('LIVEKIT_API_SECRET', 'secret');
  });

  it("polls the ticket and passes the runner's view on", async () => {
    mockPoll.mockResolvedValue(reply('tickets_poll_searching_requeued'));
    const res = await status(TICKET);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(mockPoll).toHaveBeenCalledWith(TICKET);
    expect(body.view).toMatchObject({ kind: 'searching', requeued: true });
    expect(body.ticket).toBe(TICKET);
  });

  it('an expired ticket gets no code (Q1: waiting is not paid)', async () => {
    mockPoll.mockResolvedValue(reply('tickets_poll_expired'));
    expect((await (await status(TICKET)).json()).view).toEqual({ kind: 'expired' });
  });

  it('unknown ticket: 404', async () => {
    mockPoll.mockResolvedValue(reply('tickets_poll_unknown'));
    expect((await status('00000000-0000-4000-8000-000000000000')).status).toBe(404);
  });

  it('a body that is not a ticket token never reaches the runner', async () => {
    expect((await status('../links')).status).toBe(400);
    expect((await status(undefined)).status).toBe(400);
    expect(mockPoll).not.toHaveBeenCalled();
  });

  it('three people behind one IP polling every 2 s for 2 minutes are never limited (R37)', async () => {
    mockPoll.mockResolvedValue(reply('tickets_poll_searching_requeued'));
    vi.useFakeTimers();
    const tickets = [1, 2, 3].map((n) => `${n}0000000-0000-4000-8000-00000000000${n}`);
    for (let s = 0; s < 120; s += 2) {
      vi.setSystemTime(Date.UTC(2026, 10, 2) + s * 1000);
      for (const t of tickets) expect((await status(t, '10.9.9.9')).status).toBe(200);
    }
  });

  it('more than 60 polls a minute for one ticket: 429', async () => {
    mockPoll.mockResolvedValue(reply('tickets_poll_searching_requeued'));
    const t = '40000000-0000-4000-8000-000000000004';
    for (let i = 0; i < 60; i++) expect((await status(t)).status).toBe(200);
    expect((await status(t)).status).toBe(429);
  });
});
