// Integration tests for the meeting start link (bot-config pool) flow.
// Runs against the live stack: `make test-integration` / pnpm test:api.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, test } from 'node:test';

const BASE_URL = process.env.CONCIERGE_BASE_URL ?? 'http://localhost:3000';
const createdRooms = new Set();
let sessionCookie = '';

const SCOPE_A = `slpool-a-${randomUUID().slice(0, 8)}`;
const SCOPE_B = `slpool-b-${randomUUID().slice(0, 8)}`;
const GREETING_A = `Hello from pool config A ${SCOPE_A}`;
const GREETING_B = `Hello from pool config B ${SCOPE_B}`;

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
  const setCookie = res.headers.get('set-cookie') ?? '';
  const match = setCookie.match(/console-session=[^;]+/);
  if (!match) throw new Error('No console-session cookie in login response');
  sessionCookie = match[0];
}

async function jsonRequest(path, options = {}) {
  const url = `${BASE_URL}${path}`;
  const headers = new Headers(options.headers ?? {});
  if (options.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (sessionCookie && !headers.has('Cookie') && !options.noAuth) {
    headers.set('Cookie', sessionCookie);
  }
  const response = await fetch(url, { ...options, headers });
  let text = '';
  try {
    text = await response.text();
  } catch {
    text = '';
  }
  let json = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
  }
  return { response, text, json };
}

async function putConfig(scope, greeting) {
  const { response, text } = await jsonRequest('/api/console/config', {
    method: 'PUT',
    body: JSON.stringify({ scope, greeting }),
  });
  assert.equal(response.status, 200, `config PUT for ${scope} failed: ${text}`);
}

async function pollForBot(roomName, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const { response, json } = await jsonRequest(
      `/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`
    );
    if (response.status === 200 && (json?.bots?.length ?? 0) > 0) {
      return json.bots;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  return [];
}

before(async () => {
  await loginForTest();
  await putConfig(SCOPE_A, GREETING_A);
  await putConfig(SCOPE_B, GREETING_B);
});

after(async () => {
  for (const room of createdRooms) {
    await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(room)}`, { method: 'DELETE' });
  }
  // bot_config has no DELETE — the uuid-unique test scopes are harmless residue.
});

test('configs list includes the pool scopes', async () => {
  const { response, json, text } = await jsonRequest('/api/console/configs');
  assert.equal(response.status, 200, text);
  const scopes = json.configs.map((c) => c.scope);
  assert.ok(scopes.includes(SCOPE_A), `missing ${SCOPE_A}`);
  assert.ok(scopes.includes(SCOPE_B), `missing ${SCOPE_B}`);
});

test('generate start link requires a non-empty pool', async () => {
  const { response } = await jsonRequest('/api/console/start-link', {
    method: 'POST',
    body: JSON.stringify({ pool: [] }),
  });
  assert.equal(response.status, 400);
});

test('generate + click: fresh room, bot joins, config comes from the pool', async () => {
  const gen = await jsonRequest('/api/console/start-link', {
    method: 'POST',
    body: JSON.stringify({ pool: [SCOPE_A, SCOPE_B] }),
  });
  assert.equal(gen.response.status, 200, gen.text);
  const { token, url } = gen.json;
  assert.ok(token, 'missing token');
  assert.ok(url.includes('/start/'), `unexpected url ${url}`);

  // Click is PUBLIC — no console cookie.
  const click = await jsonRequest('/api/start-link', {
    method: 'POST',
    body: JSON.stringify({ token }),
    noAuth: true,
  });
  assert.equal(click.response.status, 200, click.text);
  const roomName = click.json.roomName;
  assert.ok(roomName?.startsWith('link-'), `unexpected room ${roomName}`);
  assert.equal(click.json.url, `/rooms/${encodeURIComponent(roomName)}`);
  createdRooms.add(roomName);

  // Room exists.
  const rooms = await jsonRequest('/api/concierge/rooms');
  assert.ok(
    rooms.json.rooms.some((r) => r.name === roomName),
    `room ${roomName} not listed`
  );

  // The room's materialized config is one of the pool configs.
  const cfg = await jsonRequest(`/api/console/config?scope=${encodeURIComponent(roomName)}`);
  assert.equal(cfg.response.status, 200, cfg.text);
  assert.ok(
    [GREETING_A, GREETING_B].includes(cfg.json.greeting),
    `room config greeting "${cfg.json.greeting}" not from the pool`
  );

  // The bot actually joins.
  const bots = await pollForBot(roomName);
  assert.ok(bots.length > 0, `no bot joined ${roomName} within timeout`);
});

test('second click provisions a distinct room', async () => {
  const gen = await jsonRequest('/api/console/start-link', {
    method: 'POST',
    body: JSON.stringify({ pool: [SCOPE_A] }),
  });
  assert.equal(gen.response.status, 200, gen.text);

  const clicks = [];
  for (let i = 0; i < 2; i++) {
    const click = await jsonRequest('/api/start-link', {
      method: 'POST',
      body: JSON.stringify({ token: gen.json.token }),
      noAuth: true,
    });
    assert.equal(click.response.status, 200, click.text);
    createdRooms.add(click.json.roomName);
    clicks.push(click.json.roomName);
  }
  assert.notEqual(clicks[0], clicks[1], 'two clicks must create distinct rooms');
});

test('garbage token is rejected', async () => {
  const { response } = await jsonRequest('/api/start-link', {
    method: 'POST',
    body: JSON.stringify({ token: 'not-a-real-token' }),
    noAuth: true,
  });
  assert.equal(response.status, 400);
});

test('console routes reject unauthenticated requests', async () => {
  const configs = await jsonRequest('/api/console/configs', { noAuth: true });
  assert.equal(configs.response.status, 401);
  const gen = await jsonRequest('/api/console/start-link', {
    method: 'POST',
    body: JSON.stringify({ pool: [SCOPE_A] }),
    noAuth: true,
  });
  assert.equal(gen.response.status, 401);
});

test('a start-link token cannot be used as a console session cookie', async () => {
  const gen = await jsonRequest('/api/console/start-link', {
    method: 'POST',
    body: JSON.stringify({ pool: [SCOPE_A] }),
  });
  assert.equal(gen.response.status, 200, gen.text);

  // Same signing secret, wrong purpose — middleware must reject it (sub !== 'console').
  const { response } = await jsonRequest('/api/console/configs', {
    noAuth: true,
    headers: { Cookie: `console-session=${gen.json.token}` },
  });
  assert.equal(response.status, 401, 'start-link token must NOT grant console access');
});
