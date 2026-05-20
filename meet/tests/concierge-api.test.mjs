import { AccessToken } from 'livekit-server-sdk';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { randomUUID } from 'node:crypto';
import { after, before, test } from 'node:test';

const BASE_URL = process.env.CONCIERGE_BASE_URL ?? 'http://localhost:3000';
const ROOMS_API = `${BASE_URL}/api/concierge/rooms`;
const createdRooms = new Set();
let sessionCookie = '';

function createRoomName(prefix) {
  return `${prefix}-${Date.now()}-${randomUUID().slice(0, 8)}`;
}

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
  if (sessionCookie && !headers.has('Cookie')) {
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

async function createRoom(roomName) {
  // Retry on 409: after a room delete, LiveKit may take a moment to propagate
  // the deletion, causing an immediate recreate to conflict.
  const maxAttempts = 4;
  let last = null;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    if (attempt > 0) await new Promise((r) => setTimeout(r, 250 * attempt));
    const result = await jsonRequest('/api/concierge/rooms', {
      method: 'POST',
      body: JSON.stringify({ name: roomName }),
    });
    if (result.response.status === 201) {
      createdRooms.add(roomName);
      assert.equal(result.json?.room?.name, roomName);
      return;
    }
    last = result;
    if (result.response.status !== 409) break;
  }
  assert.equal(
    last.response.status,
    201,
    `expected room create 201 for "${roomName}", got ${last.response.status} body=${last.text}`
  );
}

function webhookAuthCredentials() {
  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;
  assert.ok(apiKey, 'LIVEKIT_API_KEY must be set for webhook verification tests');
  assert.ok(apiSecret, 'LIVEKIT_API_SECRET must be set for webhook verification tests');
  return { apiKey, apiSecret };
}

async function createWebhookAuthorization(bodyText) {
  const { apiKey, apiSecret } = webhookAuthCredentials();
  const token = new AccessToken(apiKey, apiSecret);
  token.sha256 = createHash('sha256').update(bodyText).digest('base64');
  return token.toJwt();
}

async function sendVerifiedWebhookEvent(eventPayload) {
  const bodyText = JSON.stringify(eventPayload);
  const authorization = await createWebhookAuthorization(bodyText);
  const { response, text, json } = await jsonRequest('/api/concierge/webhooks/livekit', {
    method: 'POST',
    headers: {
      Authorization: authorization,
      'Content-Type': 'application/json',
    },
    body: bodyText,
  });
  assert.equal(
    response.status,
    200,
    `expected webhook endpoint to accept verified payload, got ${response.status} body=${text}`
  );
  assert.equal(json?.ok, true);
}

async function deleteRoom(roomName) {
  const { response } = await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomName)}`, {
    method: 'DELETE',
  });
  assert.ok(
    response.status === 204 || response.status === 404,
    `expected delete status 204/404 for "${roomName}", got ${response.status}`
  );
  createdRooms.delete(roomName);
}

// LiveKit's DeleteRoom API returns before async cleanup completes, so listRooms
// can still show the deleted room for a short window. Poll until it's gone.
async function waitForRoomGone(roomName) {
  await waitFor(
    async () => {
      const { response, json } = await jsonRequest('/api/concierge/rooms');
      return response.status === 200 && !json?.rooms?.some((r) => r.name === roomName);
    },
    { timeoutMs: 10_000, description: `room "${roomName}" to be absent from LiveKit` }
  );
}

async function waitForConciergeReady() {
  const timeoutMs = 60_000;
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      // Probe the unprotected login endpoint — any HTTP response means the server is up.
      const res = await fetch(`${BASE_URL}/api/console/login`, { method: 'HEAD' });
      if (res.status > 0) return;
    } catch {
      // Network error — keep polling.
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`Meet server did not become ready at ${BASE_URL} within ${timeoutMs}ms`);
}

async function waitFor(
  callback,
  { timeoutMs = 15_000, intervalMs = 500, description = 'condition' } = {}
) {
  const start = Date.now();
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const result = await callback();
    if (result) {
      return result;
    }
    if (Date.now() - start >= timeoutMs) {
      throw new Error(`Timed out waiting for ${description}`);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

before(async () => {
  await waitForConciergeReady();  // wait for server up (unprotected endpoint)
  await loginForTest();           // then acquire session cookie
});

after(async () => {
  for (const roomName of Array.from(createdRooms)) {
    try {
      await deleteRoom(roomName);
    } catch {
      // Best-effort cleanup to keep test teardown resilient.
    }
  }
});

test('room management accepts only valid minimal room name input', async () => {
  const invalidName = 'bad room name';
  const invalid = await jsonRequest('/api/concierge/rooms', {
    method: 'POST',
    body: JSON.stringify({ name: invalidName }),
  });
  assert.equal(invalid.response.status, 400);
  assert.match(
    invalid.json?.error ?? '',
    /Room name must be 1-128 characters/,
    `unexpected invalid-name error: ${invalid.text}`
  );

  const roomName = createRoomName('concierge-room');
  await createRoom(roomName);

  const list = await jsonRequest('/api/concierge/rooms');
  assert.equal(list.response.status, 200);
  assert.ok(Array.isArray(list.json?.rooms), 'rooms response missing array');
  assert.ok(
    list.json.rooms.some((room) => room.name === roomName),
    `room ${roomName} not present in room list`
  );

  const duplicate = await jsonRequest('/api/concierge/rooms', {
    method: 'POST',
    body: JSON.stringify({ name: roomName }),
  });
  assert.equal(duplicate.response.status, 409);

  await deleteRoom(roomName);
});

test('only one bot can be assigned per room and identities are unique across rooms', async () => {
  const roomA = createRoomName('concierge-bot-a');
  const roomB = createRoomName('concierge-bot-b');
  await createRoom(roomA);
  await createRoom(roomB);

  const [startA1, startA2] = await Promise.all([
    jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomA)}/bots`, { method: 'POST' }),
    jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomA)}/bots`, { method: 'POST' }),
  ]);

  const statuses = [startA1.response.status, startA2.response.status].sort((a, b) => a - b);
  assert.deepEqual(statuses, [200, 409], `unexpected parallel start statuses: ${statuses}`);

  const started = startA1.response.status === 200 ? startA1 : startA2;
  const rejected = startA1.response.status === 409 ? startA1 : startA2;
  const botIdA = started.json?.request?.botIdentity;
  assert.ok(botIdA, `missing bot identity in successful start payload: ${started.text}`);
  assert.match(rejected.json?.error ?? '', /already/i);

  const startB = await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomB)}/bots`, {
    method: 'POST',
  });
  assert.equal(startB.response.status, 200, `roomB start failed: ${startB.text}`);
  const botIdB = startB.json?.request?.botIdentity;
  assert.ok(botIdB, `missing bot identity for roomB: ${startB.text}`);
  assert.notEqual(botIdA, botIdB, 'bot identities should be unique across different rooms');

  const roomABots = await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomA)}/bots`);
  assert.equal(roomABots.response.status, 200);
  assert.equal(roomABots.json?.assignedBotIdentity, botIdA);
});

test('room and bot health are available for monitoring', async () => {
  const roomName = createRoomName('concierge-health');
  await createRoom(roomName);

  const start = await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`, {
    method: 'POST',
  });
  assert.equal(start.response.status, 200, `bot start failed: ${start.text}`);
  const startedBotIdentity = start.json?.request?.botIdentity;
  assert.ok(startedBotIdentity, `missing bot identity from start response: ${start.text}`);

  const health = await waitFor(
    async () => {
      const result = await jsonRequest(
        `/api/concierge/rooms/${encodeURIComponent(roomName)}/health`
      );
      if (result.response.status !== 200) {
        return null;
      }
      if (!result.json?.room?.exists) {
        return null;
      }
      if (!result.json?.bot?.assignedIdentity) {
        return null;
      }
      return result;
    },
    { description: 'room and bot health payload' }
  );

  assert.equal(health.response.status, 200);
  assert.equal(health.json?.room?.exists, true);
  assert.equal(health.json?.bot?.assignedIdentity, startedBotIdentity);
  assert.match(
    health.json?.bot?.status ?? '',
    /^(starting|connected_no_tracks|connected)$/,
    `unexpected bot status in health response: ${health.text}`
  );
  assert.ok(
    ['ok', 'degraded'].includes(health.json?.overallStatus),
    `unexpected overallStatus: ${health.text}`
  );
  assert.match(
    health.json?.bot?.subscriptionSignal?.status ?? '',
    /^(unknown|not_observed|observed)$/,
    `missing or invalid bot subscription signal status: ${health.text}`
  );
});

test('failed bot start releases room claim and allows a clean retry', async () => {
  const roomName = createRoomName('concierge-start-fail-cleanup');
  await createRoom(roomName);

  const forcedFailure = await jsonRequest(
    `/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`,
    {
      method: 'POST',
      headers: {
        'x-concierge-test-force-runner-failure': '1',
      },
    }
  );
  assert.equal(
    forcedFailure.response.status,
    502,
    `expected forced runner failure: ${forcedFailure.text}`
  );

  const afterFailure = await jsonRequest(
    `/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`
  );
  assert.equal(afterFailure.response.status, 200);
  assert.equal(
    afterFailure.json?.assignedBotIdentity,
    undefined,
    `room claim should be released after failed start: ${afterFailure.text}`
  );

  const retry = await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`, {
    method: 'POST',
  });
  assert.equal(retry.response.status, 200, `bot start retry should succeed: ${retry.text}`);
  assert.ok(retry.json?.request?.botIdentity, `retry start missing bot identity: ${retry.text}`);
});

test('bot remove rejects identity mismatch and preserves assigned bot claim', async () => {
  const roomName = createRoomName('concierge-remove-mismatch');
  await createRoom(roomName);

  const start = await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`, {
    method: 'POST',
  });
  assert.equal(start.response.status, 200, `bot start failed: ${start.text}`);
  const assignedBotIdentity = start.json?.request?.botIdentity;
  assert.ok(assignedBotIdentity, `missing bot identity from start response: ${start.text}`);

  const mismatchIdentity = `${assignedBotIdentity}_wrong`;
  const mismatchDelete = await jsonRequest(
    `/api/concierge/rooms/${encodeURIComponent(roomName)}/bots/${encodeURIComponent(mismatchIdentity)}`,
    {
      method: 'DELETE',
    }
  );
  assert.equal(
    mismatchDelete.response.status,
    409,
    `expected mismatch delete to be rejected: ${mismatchDelete.text}`
  );
  assert.equal(mismatchDelete.json?.assignedBotIdentity, assignedBotIdentity);

  const bots = await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`);
  assert.equal(bots.response.status, 200);
  assert.equal(
    bots.json?.assignedBotIdentity,
    assignedBotIdentity,
    `assigned bot claim should remain after mismatch delete: ${bots.text}`
  );
});

test('room delete clears bot claim and supports clean recreate/start cycle', async () => {
  const roomName = createRoomName('concierge-delete-cleanup');
  await createRoom(roomName);

  const firstStart = await jsonRequest(
    `/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`,
    {
      method: 'POST',
    }
  );
  assert.equal(firstStart.response.status, 200, `initial bot start failed: ${firstStart.text}`);
  const firstBotIdentity = firstStart.json?.request?.botIdentity;
  assert.ok(firstBotIdentity, `missing first bot identity: ${firstStart.text}`);

  // Wait for the bot to actually join the LiveKit room before deleting. If we
  // delete before it connects, the bot joins a non-existent room, which
  // LiveKit --dev auto-creates, causing the subsequent createRoom to 409.
  // Once the bot is in the room, deleteRoom sends it a terminal ROOM_DELETED
  // disconnect, which the LiveKit SDK does not retry.
  await waitFor(
    async () => {
      const { response, json } = await jsonRequest(
        `/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`
      );
      return response.status === 200 && json?.bots?.some((b) => b.identity === firstBotIdentity);
    },
    { timeoutMs: 15_000, description: `bot ${firstBotIdentity} to join room before delete` }
  );

  await deleteRoom(roomName);
  await waitForRoomGone(roomName);
  await createRoom(roomName);

  const secondStart = await jsonRequest(
    `/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`,
    {
      method: 'POST',
    }
  );
  assert.equal(
    secondStart.response.status,
    200,
    `bot start should succeed after room recreate: ${secondStart.text}`
  );
  const secondBotIdentity = secondStart.json?.request?.botIdentity;
  assert.ok(secondBotIdentity, `missing second bot identity: ${secondStart.text}`);
  assert.notEqual(
    secondBotIdentity,
    firstBotIdentity,
    'recreated room should not reuse stale bot identity assignment'
  );
});

test('verified bot-leave webhook reconciles assigned bot claim for room', async () => {
  const roomName = createRoomName('concierge-webhook-reconcile');
  await createRoom(roomName);

  const start = await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`, {
    method: 'POST',
  });
  assert.equal(start.response.status, 200, `bot start failed: ${start.text}`);
  const botIdentity = start.json?.request?.botIdentity;
  assert.ok(botIdentity, `missing bot identity from start response: ${start.text}`);

  await sendVerifiedWebhookEvent({
    event: 'participant_left',
    room: { name: roomName },
    participant: { identity: botIdentity },
  });

  const botsAfterWebhook = await waitFor(
    async () => {
      const result = await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`);
      if (result.response.status !== 200) {
        return null;
      }
      if (result.json?.assignedBotIdentity !== undefined) {
        return null;
      }
      return result;
    },
    { description: 'assigned bot claim cleared after verified bot-leave webhook' }
  );
  assert.equal(botsAfterWebhook.response.status, 200);
  assert.equal(botsAfterWebhook.json?.assignedBotIdentity, undefined);
});
