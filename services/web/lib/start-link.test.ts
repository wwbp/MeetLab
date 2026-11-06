import { beforeAll, describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { SignJWT } from 'jose';
import { linkSettings, resolveLinkParam, signStartLink, verifyStartLink } from './start-link';

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

describe('resolveLinkParam', () => {
  const legacyId = (token: string) =>
    createHash('sha256').update(token).digest('base64url').slice(0, 22);

  it('takes a stored link ID as it is', async () => {
    expect(await resolveLinkParam('Qm9vdHN0cmFwTGlua0lEMQ')).toEqual({
      linkId: 'Qm9vdHN0cmFwTGlua0lEMQ',
    });
  });

  it('gives an old JWT link an ID of the same shape, and its pool', async () => {
    const token = await signStartLink(['friendly', 'strict']);
    const resolved = await resolveLinkParam(token);
    expect(resolved).toEqual({ linkId: legacyId(token), legacyPool: ['friendly', 'strict'] });
    expect(resolved?.linkId).toMatch(/^[A-Za-z0-9_-]{22}$/);
  });

  it('hashes the URL-decoded token: an encoded and a decoded link are one link', async () => {
    const token = await signStartLink(['friendly']);
    const encoded = encodeURIComponent(token).replace(/\./g, '%2E');
    expect(encoded).not.toBe(token);
    expect(await resolveLinkParam(encoded)).toEqual(await resolveLinkParam(token));
  });

  it('refuses anything else', async () => {
    for (const bad of [
      '',
      'not-a-jwt',
      'Qm9vdHN0cmFwTGlua0lEM',
      'Qm9vdHN0cmFwTGlua0lEMQ=',
      '%E0%A4%A',
      'a'.repeat(23),
    ]) {
      expect(await resolveLinkParam(bad), bad).toBeNull();
    }
  });
});

describe('linkSettings', () => {
  const base = { name: 'Pilot 3', pool: ['friendly'] };

  it('fills the defaults: size 1, min size = size, no delay, 5 min max wait, 60 s to confirm, no password', () => {
    expect(linkSettings(base)).toEqual({
      link: {
        name: 'Pilot 3',
        pool: ['friendly'],
        size: 1,
        min_size: 1,
        delay_s: 0,
        max_wait_s: 300,
        ready_check_s: 60,
      },
    });
    expect(linkSettings({ ...base, size: 3 })).toMatchObject({ link: { size: 3, min_size: 3 } });
  });

  it('keeps every value given, and the password when there is one', () => {
    const link = {
      ...base,
      size: 3,
      min_size: 2,
      delay_s: 10,
      max_wait_s: 600,
      ready_check_s: 45,
      password: 'blue-river',
    };
    expect(linkSettings(link)).toEqual({ link });
    expect(linkSettings({ ...base, password: '' })).toEqual(linkSettings(base));
  });

  it('trims the name, and names an unnamed request (older callers send only the pool)', () => {
    expect(linkSettings({ ...base, name: '  Pilot 3 ' })).toMatchObject({
      link: { name: 'Pilot 3' },
    });
    expect(linkSettings({ pool: ['friendly'] })).toMatchObject({ link: { name: 'Untitled link' } });
  });

  it('refuses values out of range', () => {
    for (const bad of [
      { name: '  ' },
      { pool: [] },
      { pool: [''] },
      { size: 0 },
      { size: 9 },
      { size: 2.5 },
      { size: '3' },
      { size: 3, min_size: 4 },
      { min_size: 0 },
      { delay_s: -1 },
      { delay_s: 301 },
      { max_wait_s: 59 },
      { max_wait_s: 1801 },
      { ready_check_s: 9 },
      { ready_check_s: 301 },
      { password: 42 },
    ]) {
      expect(linkSettings({ ...base, ...bad }), JSON.stringify(bad)).toHaveProperty('error');
    }
  });

  it('refuses a body that is not an object', () => {
    for (const bad of [null, 'x', 3, []]) expect(linkSettings(bad)).toHaveProperty('error');
  });
});
