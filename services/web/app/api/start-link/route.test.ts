import fs from 'node:fs';
import path from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { resolveLinkParam, signStartLink } from '@/lib/start-link';
import { COPY } from '@/lib/waiting';

const mm = vi.hoisted(() => ({ createTicket: vi.fn(), publicLink: vi.fn() }));
vi.mock('@/lib/matchmaking-client', () => mm);
vi.mock('@/lib/concierge/events-store', () => ({ pushConciergeEvent: vi.fn() }));

import { POST } from './route';

const reply = (name: string) =>
  JSON.parse(
    fs.readFileSync(
      path.join(__dirname, '../../../../../tests/fixtures/matchmaking', `${name}.json`),
      'utf8',
    ),
  ).response;

const LINK = 'Qm9vdHN0cmFwTGlua0lEMQ';
const PID = '5f8e2a1b3c4d5e6f7a8b9c0d';
const group = (size = 3) => ({ status: 200, body: { active: true, needs_password: true, size } });

let ipN = 0;
const freshIp = () => `10.1.${Math.floor(++ipN / 250)}.${ipN % 250}`;

const start = (body: object, ip = freshIp()) =>
  POST(
    new Request('http://localhost/api/start-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-forwarded-for': ip },
      body: JSON.stringify(body),
    }),
  );

describe('POST /api/start-link', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    vi.stubEnv('LIVEKIT_API_SECRET', 'secret');
  });

  it("an old JWT link (size 1): no ID, a legacy ticket, and today's reply {url, roomName}", async () => {
    const token = await signStartLink(['global']);
    mm.createTicket.mockResolvedValue(reply('tickets_create_size1'));
    const res = await start({ token, participant_id: PID });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      url: '/rooms/link-mh0abc12-1a2b3c4d',
      roomName: 'link-mh0abc12-1a2b3c4d',
    });
    const { linkId } = (await resolveLinkParam(token))!;
    expect(mm.createTicket).toHaveBeenCalledWith({
      link_id: linkId,
      participant_id: null,
      legacy: { pool: ['global'] },
    });
    expect(mm.publicLink).not.toHaveBeenCalled();
  });

  it('size 1 whose room could not be set up: 502, as today', async () => {
    const token = await signStartLink(['global']);
    const failed = reply('tickets_create_size1');
    failed.body.view = { kind: 'starting', room: null, starts_in_ms: 0 };
    mm.createTicket.mockResolvedValue(failed);
    expect((await start({ token })).status).toBe(502);
  });

  it('size 1 with a start delay gets a ticket to wait on', async () => {
    mm.publicLink.mockResolvedValue({
      status: 200,
      body: { active: true, needs_password: false, size: 1 },
    });
    const delayed = reply('tickets_create_size1');
    delayed.body.view = { kind: 'starting', room: 'link-x', starts_in_ms: 30_000 };
    mm.createTicket.mockResolvedValue(delayed);
    const body = await (await start({ token: LINK })).json();
    expect(body.ticket).toBe(delayed.body.ticket);
    expect(body.view.starts_in_ms).toBe(30_000);
  });

  it('a group link needs a valid Prolific ID, then returns the ticket and its view', async () => {
    mm.publicLink.mockResolvedValue(group());
    expect((await start({ token: LINK })).status).toBe(400);
    expect((await start({ token: LINK, participant_id: 'not-an-id' })).status).toBe(400);
    expect((await start({ token: LINK, participant_id: 42 })).status).toBe(400);
    expect(mm.createTicket).not.toHaveBeenCalled();

    mm.createTicket.mockResolvedValue(reply('tickets_create_group'));
    const res = await start({
      token: LINK,
      participant_id: ` ${PID.toUpperCase()} `,
      password: 'blue-river',
    });
    expect(res.status).toBe(200);
    expect(mm.createTicket).toHaveBeenCalledWith({
      link_id: LINK,
      participant_id: PID,
      password: 'blue-river',
    });
    expect(await res.json()).toMatchObject({
      ticket: '6f1c2b3a-4d5e-4f60-8a7b-9c0d1e2f3a4b',
      view: { kind: 'searching' },
    });
  });

  it('a garbage link, or an ID the runner does not know: 400', async () => {
    expect((await start({ token: 'nope' })).status).toBe(400);
    mm.publicLink.mockResolvedValue(reply('links_public_unknown'));
    expect((await start({ token: 'AAAAAAAAAAAAAAAAAAAAAA' })).status).toBe(400);
    expect(mm.createTicket).not.toHaveBeenCalled();
  });

  it('a stopped link: 410', async () => {
    mm.publicLink.mockResolvedValue(group());
    mm.createTicket.mockResolvedValue(reply('tickets_create_closed'));
    expect((await start({ token: LINK, participant_id: PID, password: 'blue-river' })).status).toBe(
      410,
    );
  });

  it('5 wrong passwords in a minute lock that IP out of that link for a minute (R29)', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.UTC(2026, 10, 2, 12));
    mm.publicLink.mockResolvedValue(group());
    mm.createTicket.mockResolvedValue(reply('tickets_create_wrong_password'));
    const ip = freshIp();
    const attempt = () => start({ token: LINK, participant_id: PID, password: 'nope' }, ip);
    for (let i = 0; i < 5; i++) {
      const res = await attempt();
      expect(res.status).toBe(403);
      expect((await res.json()).error).toBe(COPY.wrongPassword);
    }
    const locked = await attempt();
    expect(locked.status).toBe(429);
    expect((await locked.json()).error).toBe(COPY.locked);
    expect(mm.createTicket).toHaveBeenCalledTimes(5);

    // Another IP is not locked.
    expect((await start({ token: LINK, participant_id: PID, password: 'nope' })).status).toBe(403);

    vi.setSystemTime(Date.UTC(2026, 10, 2, 12, 1, 1));
    mm.createTicket.mockResolvedValue(reply('tickets_create_group'));
    expect(
      (await start({ token: LINK, participant_id: PID, password: 'blue-river' }, ip)).status,
    ).toBe(200);
  });

  it('8 people behind one IP can all join a size-8 link; 5 × size per minute (R37)', async () => {
    mm.publicLink.mockResolvedValue(group(8));
    mm.createTicket.mockResolvedValue(reply('tickets_create_group'));
    const ip = freshIp();
    for (let i = 0; i < 40; i++) {
      expect((await start({ token: LINK, participant_id: PID, password: 'x' }, ip)).status).toBe(
        200,
      );
    }
    expect((await start({ token: LINK, participant_id: PID, password: 'x' }, ip)).status).toBe(429);
  });

  it("size 1 keeps today's 5 starts per minute per IP", async () => {
    const token = await signStartLink(['global']);
    mm.createTicket.mockResolvedValue(reply('tickets_create_size1'));
    const ip = freshIp();
    for (let i = 0; i < 5; i++) expect((await start({ token }, ip)).status).toBe(200);
    expect((await start({ token }, ip)).status).toBe(429);
  });
});
