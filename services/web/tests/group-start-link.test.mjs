// Integration tests for group start links (stored links, tickets, the ready check) against
// the live stack: pnpm test:api. The R21 test restarts meet; it runs only when
// RESTART_MEET_CMD is set, e.g.
//   RESTART_MEET_CMD='docker compose -f .devcontainer/docker-compose.yml restart meet'
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import { createHash, createHmac, randomBytes, randomUUID } from 'node:crypto';
import { after, before, test } from 'node:test';

const BASE_URL = process.env.CONCIERGE_BASE_URL ?? 'http://localhost:3000';
const SECRET = process.env.LIVEKIT_API_SECRET ?? 'secret';
const createdRooms = new Set();
const createdLinks = new Set();
let sessionCookie = '';

const SCOPE = `glpool-${randomUUID().slice(0, 8)}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const prolificId = () => randomBytes(12).toString('hex'); // 24 hex

async function loginForTest() {
  const password = process.env.CONSOLE_PASSWORD ?? 'changeme';
  const res = await fetch(`${BASE_URL}/api/console/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    throw new Error(`Console login failed (${res.status}) — is CONSOLE_PASSWORD set correctly?`);
  }
  const match = (res.headers.get('set-cookie') ?? '').match(/console-session=[^;]+/);
  if (!match) throw new Error('No console-session cookie in login response');
  sessionCookie = match[0];
}

async function jsonRequest(path, options = {}) {
  const headers = new Headers(options.headers ?? {});
  if (options.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (sessionCookie && !headers.has('Cookie') && !options.noAuth) {
    headers.set('Cookie', sessionCookie);
  }
  const response = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  const text = await response.text().catch(() => '');
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  return { response, text, json };
}

const post = (path, body, noAuth = true) =>
  jsonRequest(path, { method: 'POST', body: JSON.stringify(body), noAuth });

async function createLink(settings) {
  const gen = await post('/api/console/start-link', { pool: [SCOPE], ...settings }, false);
  assert.equal(gen.response.status, 200, gen.text);
  createdLinks.add(gen.json.token);
  return gen.json.token;
}

async function join(token, participant_id) {
  const r = await post('/api/start-link', { token, participant_id });
  assert.equal(r.response.status, 200, r.text);
  return r.json;
}

async function poll(ticket) {
  const r = await post('/api/start-link/status', { ticket });
  assert.equal(r.response.status, 200, r.text);
  return r.json;
}

async function ready(ticket) {
  const r = await post('/api/start-link/ready', { ticket });
  assert.equal(r.response.status, 200, r.text);
  return r.json;
}

// An old-style link: an HS256 JWT { purpose, pool } signed with LIVEKIT_API_SECRET (lib/start-link.ts).
function signLegacyLink(pool) {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
  const body = `${b64({ alg: 'HS256' })}.${b64({
    purpose: 'meetlab-start-link',
    pool,
    iat: Math.floor(Date.now() / 1000),
  })}`;
  return `${body}.${createHmac('sha256', SECRET).update(body).digest('base64url')}`;
}

before(async () => {
  await loginForTest();
  const { response, text } = await jsonRequest('/api/console/config', {
    method: 'PUT',
    body: JSON.stringify({ scope: SCOPE, greeting: `Hello from group pool ${SCOPE}` }),
  });
  assert.equal(response.status, 200, `config PUT for ${SCOPE} failed: ${text}`);
});

after(async () => {
  for (const id of createdLinks) {
    await jsonRequest(`/api/console/start-link/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ active: false }),
    });
  }
  for (const room of createdRooms) {
    await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(room)}`, { method: 'DELETE' });
  }
  // bot_config has no DELETE (as in start-link.test.mjs): the uuid-unique scope is residue.
});

test('R3/R30: an old JWT link starts a 1-on-1 room, is listed as legacy, and can be stopped', async () => {
  const token = signLegacyLink([SCOPE]);
  const click = await post('/api/start-link', { token });
  assert.equal(click.response.status, 200, click.text);
  const roomName = click.json.roomName;
  assert.ok(roomName?.startsWith('link-'), `unexpected room ${roomName}`);
  assert.equal(click.json.url, `/rooms/${encodeURIComponent(roomName)}`);
  createdRooms.add(roomName);

  const linkId = createHash('sha256').update(token).digest('base64url').slice(0, 22);
  const list = await jsonRequest('/api/console/start-link');
  assert.equal(list.response.status, 200, list.text);
  const row = list.json.links.find((l) => l.link_id === linkId);
  assert.ok(row, `legacy link ${linkId} not listed`);
  assert.equal(row.legacy, true);
  assert.equal(row.size, 1);
  assert.deepEqual(row.pool, [SCOPE]);

  const stop = await jsonRequest(`/api/console/start-link/${linkId}`, {
    method: 'PATCH',
    body: JSON.stringify({ active: false }),
  });
  assert.equal(stop.response.status, 200, stop.text);
  assert.deepEqual(stop.json, { link_id: linkId, active: false });

  const again = await post('/api/start-link', { token });
  assert.equal(again.response.status, 410, again.text);
});

test('group of 2: both pass the ready check and start in the same room, one session', async () => {
  const token = await createLink({ name: 'it group 2', size: 2, delay_s: 0, ready_check_s: 10 });
  const a = await join(token, prolificId());
  assert.equal(a.view.kind, 'searching', JSON.stringify(a));
  assert.equal(a.view.size, 2);
  const b = await join(token, prolificId());
  assert.equal(b.view.kind, 'ready_check', JSON.stringify(b));
  assert.equal((await poll(a.ticket)).view.kind, 'ready_check');

  const ra = await ready(a.ticket);
  assert.deepEqual([ra.view.kind, ra.view.pressed], ['ready_check', true], JSON.stringify(ra));
  const rb = await ready(b.ticket);
  assert.equal(rb.view.kind, 'starting', JSON.stringify(rb));
  const room = rb.view.room;
  assert.ok(room?.startsWith('link-'), `no room in ${JSON.stringify(rb)}`);
  createdRooms.add(room);
  const pa = await poll(a.ticket);
  assert.equal(pa.view.kind, 'starting', JSON.stringify(pa));
  assert.equal(pa.view.room, room, 'both participants must get the same room');

  const meetings = await jsonRequest('/api/meetings?limit=100');
  assert.equal(meetings.response.status, 200, meetings.text);
  const running = meetings.json.conversations.filter(
    (c) => c.room_name === room && c.status === 'running',
  );
  assert.equal(running.length, 1, `expected one running session in ${room}, got ${running.length}`);
  assert.equal(running[0].meta?.bot_config_scope, SCOPE);
});

test('missed ready check: the one who did not press is out, the other is requeued', async () => {
  const token = await createLink({ name: 'it missed', size: 2, delay_s: 0, ready_check_s: 10 });
  const a = await join(token, prolificId());
  const b = await join(token, prolificId());
  assert.equal(b.view.kind, 'ready_check', JSON.stringify(b));
  assert.equal((await ready(a.ticket)).view.pressed, true);

  await sleep(b.view.deadline_in_ms + 1500);
  const pb = await poll(b.ticket);
  assert.deepEqual(pb.view, { kind: 'missed' });
  const pa = await poll(a.ticket);
  assert.equal(pa.view.kind, 'searching', JSON.stringify(pa));
  assert.equal(pa.view.requeued, true);
});

test('pool check: a link with an unknown bot setting is refused', async () => {
  const missing = `glmissing-${randomUUID().slice(0, 8)}`;
  const gen = await post('/api/console/start-link', { pool: [SCOPE, missing], size: 2 }, false);
  assert.equal(gen.response.status, 400, gen.text);
  assert.match(gen.json?.error ?? '', new RegExp(`unknown bot settings: ${missing}`));
});

// Last: it restarts meet.
test(
  'R21: a waiting participant keeps their ticket and view across a meet restart',
  { skip: !process.env.RESTART_MEET_CMD && 'set RESTART_MEET_CMD to restart meet' },
  async () => {
    const token = await createLink({ name: 'it restart', size: 3 });
    const pid = prolificId();
    const t = await join(token, pid);
    assert.equal(t.view.kind, 'searching', JSON.stringify(t));

    execSync(process.env.RESTART_MEET_CMD, { stdio: 'inherit' });
    const deadline = Date.now() + 180_000;
    for (;;) {
      const ok = await fetch(BASE_URL).then((r) => r.ok, () => false);
      if (ok) break;
      assert.ok(Date.now() < deadline, 'meet did not come back within 3 minutes');
      await sleep(2000);
    }

    const p = await poll(t.ticket);
    assert.equal(p.ticket, t.ticket);
    assert.equal(p.participant_id, pid);
    assert.deepEqual([p.view.kind, p.view.size, p.view.requeued], ['searching', 3, false]);
    const rejoin = await join(token, pid);
    assert.equal(rejoin.ticket, t.ticket, 'joining again after the restart must keep the ticket');
  },
);
