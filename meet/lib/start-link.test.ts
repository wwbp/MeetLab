import { beforeAll, describe, expect, it } from 'vitest';
import { SignJWT } from 'jose';
import {
  generateStartRoomName,
  pickUniform,
  signStartLink,
  verifyStartLink,
} from './start-link';

// Matches ROOM_NAME_PATTERN in app/api/concierge/rooms/route.ts
const ROOM_NAME_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/;

beforeAll(() => {
  process.env.LIVEKIT_API_SECRET = 'test-secret-test-secret-test-secret';
});

describe('signStartLink / verifyStartLink', () => {
  it('roundtrips a pool', async () => {
    const token = await signStartLink(['friendly', 'strict']);
    expect(await verifyStartLink(token)).toEqual(['friendly', 'strict']);
  });

  it('rejects a tampered token', async () => {
    const token = await signStartLink(['friendly']);
    expect(await verifyStartLink(token.slice(0, -2) + 'xx')).toBeNull();
  });

  it('rejects garbage', async () => {
    expect(await verifyStartLink('not-a-jwt')).toBeNull();
  });

  it('rejects a same-secret JWT without the start-link purpose (e.g. console session)', async () => {
    const secret = new TextEncoder().encode(process.env.LIVEKIT_API_SECRET);
    const consoleJwt = await new SignJWT({ sub: 'console' })
      .setProtectedHeader({ alg: 'HS256' })
      .setIssuedAt()
      .sign(secret);
    expect(await verifyStartLink(consoleJwt)).toBeNull();
  });

  it('rejects a signed token whose pool is empty or malformed', async () => {
    const secret = new TextEncoder().encode(process.env.LIVEKIT_API_SECRET);
    for (const pool of [[], 'friendly', [1, 2], [''], undefined]) {
      const bad = await new SignJWT({ purpose: 'meetlab-start-link', pool })
        .setProtectedHeader({ alg: 'HS256' })
        .sign(secret);
      expect(await verifyStartLink(bad), JSON.stringify(pool)).toBeNull();
    }
  });

  it('signStartLink rejects an empty pool', async () => {
    await expect(signStartLink([])).rejects.toThrow();
  });
});

describe('pickUniform', () => {
  it('always returns a member', () => {
    const items = ['a', 'b', 'c'];
    for (let i = 0; i < 100; i++) {
      expect(items).toContain(pickUniform(items));
    }
  });

  it('is roughly uniform over 3 items x 3000 draws', () => {
    const counts: Record<string, number> = { a: 0, b: 0, c: 0 };
    for (let i = 0; i < 3000; i++) {
      counts[pickUniform(['a', 'b', 'c'])]++;
    }
    // Expected ~1000 each; loose bounds to avoid flakes (p(<800) is astronomically small).
    for (const k of ['a', 'b', 'c']) {
      expect(counts[k]).toBeGreaterThan(800);
    }
  });
});

describe('generateStartRoomName', () => {
  it('generates valid, distinct room names', () => {
    const names = new Set<string>();
    for (let i = 0; i < 200; i++) {
      const name = generateStartRoomName();
      expect(name).toMatch(ROOM_NAME_PATTERN);
      names.add(name);
    }
    expect(names.size).toBe(200);
  });
});
