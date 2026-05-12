import { AccessToken } from 'livekit-server-sdk';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import test from 'node:test';

const BASE_URL = process.env.CONCIERGE_BASE_URL ?? 'http://localhost:3000';
const ROOM_COUNT = 5;
const MOCK_USER_COUNT_PER_ROOM = 5;

function createRoomName(index) {
  return `concierge-load-${Date.now()}-${index}-${randomUUID().slice(0, 8)}`;
}

async function jsonRequest(path, options = {}) {
  const url = `${BASE_URL}${path}`;
  const headers = new Headers(options.headers ?? {});
  if (options.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(url, { ...options, headers });
  const text = await response.text().catch(() => '');
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
    `expected verified webhook request to succeed, got ${response.status} body=${text}`
  );
  assert.equal(json?.ok, true);
}

async function waitFor(
  callback,
  { timeoutMs = 20_000, intervalMs = 500, description = 'condition' } = {}
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

async function deleteRoom(roomName) {
  await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomName)}`, {
    method: 'DELETE',
  }).catch(() => null);
}

async function startBotWithRetry(roomName, { maxAttempts = 3 } = {}) {
  let lastResult = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const result = await jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`, {
      method: 'POST',
    });
    if (result.response.status === 200) {
      return result;
    }

    lastResult = result;
    const transientTimeoutFailure =
      result.response.status === 502 && (result.json?.error ?? '').includes('aborted');
    if (!transientTimeoutFailure || attempt === maxAttempts) {
      break;
    }

    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  throw new Error(`bot start failed after retries for "${roomName}": ${lastResult?.text ?? ''}`);
}

test('mock load: 5 rooms with 5 mock users and 1 bot each', async () => {
  const rooms = Array.from({ length: ROOM_COUNT }, (_, index) => createRoomName(index));

  try {
    const roomCreates = await Promise.all(
      rooms.map((name) =>
        jsonRequest('/api/concierge/rooms', {
          method: 'POST',
          body: JSON.stringify({ name }),
        })
      )
    );
    for (const createResult of roomCreates) {
      assert.equal(createResult.response.status, 201, `room create failed: ${createResult.text}`);
    }

    const startResults = [];
    for (const roomName of rooms) {
      const startResult = await startBotWithRetry(roomName);
      assert.equal(startResult.response.status, 200, `bot start failed: ${startResult.text}`);
      startResults.push(startResult);
    }
    const botIdentities = startResults.map((result) => result.json?.request?.botIdentity);
    assert.equal(new Set(botIdentities).size, ROOM_COUNT, 'bot identities must be unique per room');

    const userJoinWebhooks = [];
    for (const roomName of rooms) {
      for (let userIndex = 0; userIndex < MOCK_USER_COUNT_PER_ROOM; userIndex += 1) {
        userJoinWebhooks.push(
          sendVerifiedWebhookEvent({
            event: 'participant_joined',
            room: { name: roomName },
            participant: { identity: `mock_user_${roomName}_${userIndex}` },
          })
        );
      }
    }
    await Promise.all(userJoinWebhooks);

    await Promise.all(
      rooms.map((roomName, roomIndex) =>
        sendVerifiedWebhookEvent({
          event: 'track_published',
          room: { name: roomName },
          participant: { identity: botIdentities[roomIndex] },
          track: { sid: `TR_${randomUUID().replace(/-/g, '').slice(0, 12)}` },
        })
      )
    );

    const healthResults = await Promise.all(
      rooms.map((roomName) =>
        jsonRequest(`/api/concierge/rooms/${encodeURIComponent(roomName)}/health`)
      )
    );
    for (const healthResult of healthResults) {
      assert.equal(
        healthResult.response.status,
        200,
        `health request failed: ${healthResult.text}`
      );
      assert.ok(
        ['starting', 'connected_no_tracks', 'connected'].includes(healthResult.json?.bot?.status),
        `unexpected bot health status: ${healthResult.text}`
      );
    }

    const events = await jsonRequest('/api/concierge/events?limit=250');
    assert.equal(events.response.status, 200, `events request failed: ${events.text}`);
    const joinEventsForLoad = (events.json?.events ?? []).filter(
      (entry) =>
        entry.event === 'participant_joined' &&
        rooms.includes(entry.roomName) &&
        typeof entry.participantIdentity === 'string' &&
        entry.participantIdentity.startsWith('mock_user_')
    );
    assert.ok(
      joinEventsForLoad.length >= ROOM_COUNT * MOCK_USER_COUNT_PER_ROOM,
      `expected at least ${ROOM_COUNT * MOCK_USER_COUNT_PER_ROOM} mock participant_joined events, got ${joinEventsForLoad.length}`
    );

    await Promise.all(
      rooms.map((roomName, roomIndex) =>
        sendVerifiedWebhookEvent({
          event: 'participant_left',
          room: { name: roomName },
          participant: { identity: botIdentities[roomIndex] },
        })
      )
    );

    await Promise.all(
      rooms.map((roomName) =>
        waitFor(
          async () => {
            const result = await jsonRequest(
              `/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`
            );
            if (result.response.status !== 200) {
              return null;
            }
            return result.json?.assignedBotIdentity === undefined ? result : null;
          },
          { description: `claim cleanup after bot-leave webhook for room ${roomName}` }
        )
      )
    );
  } finally {
    await Promise.all(rooms.map((roomName) => deleteRoom(roomName)));
  }
});
