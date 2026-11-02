import { createHash, randomInt, randomUUID } from 'node:crypto';
import { SignJWT, jwtVerify } from 'jose';
import type { NewLink } from '@/lib/matchmaking-client';

/**
 * Stateless "meeting start link" tokens.
 *
 * A start link embeds a signed JWT carrying the pool of bot-config scopes the admin
 * selected. Every click provisions a fresh room with one scope picked uniformly at
 * random. The token is signed with LIVEKIT_API_SECRET (same key as the console
 * session, see lib/console/auth.ts) and deliberately has NO expiry: all concierge
 * state in meet is in-memory, so links must survive restarts. The `purpose` claim
 * keeps these tokens from being accepted anywhere else signed with the same secret
 * (middleware requires sub === 'console' for console sessions).
 */
const PURPOSE = 'meetlab-start-link';

function signingKey(): Uint8Array {
  const secret = process.env.LIVEKIT_API_SECRET;
  if (!secret) throw new Error('LIVEKIT_API_SECRET is not set');
  return new TextEncoder().encode(secret);
}

function isValidPool(pool: unknown): pool is string[] {
  return (
    Array.isArray(pool) &&
    pool.length > 0 &&
    pool.every((s) => typeof s === 'string' && s.trim().length > 0)
  );
}

export async function signStartLink(pool: string[]): Promise<string> {
  if (!isValidPool(pool)) throw new Error('pool must be a non-empty array of scope names');
  return new SignJWT({ purpose: PURPOSE, pool })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .sign(signingKey());
}

/** Returns the pool of scopes, or null for anything invalid. */
export async function verifyStartLink(token: string): Promise<string[] | null> {
  try {
    const { payload } = await jwtVerify(token, signingKey());
    if (payload.purpose !== PURPOSE || !isValidPool(payload.pool)) return null;
    return payload.pool;
  } catch {
    return null;
  }
}

/** A stored link's ID: 22 base64url characters (16 random bytes, made by the runner). */
const LINK_ID = /^[A-Za-z0-9_-]{22}$/;

/**
 * The /start/<param> of either kind of link: a stored link's ID, or an old JWT link, whose
 * ID is the hash of its URL-decoded token (same shape, so one link however it was pasted).
 * Null for anything else.
 */
export async function resolveLinkParam(
  param: string,
): Promise<{ linkId: string; legacyPool?: string[] } | null> {
  let decoded: string;
  try {
    decoded = decodeURIComponent(param);
  } catch {
    return null;
  }
  if (LINK_ID.test(decoded)) return { linkId: decoded };
  const pool = await verifyStartLink(decoded);
  if (!pool) return null;
  const linkId = createHash('sha256').update(decoded).digest('base64url').slice(0, 22);
  return { linkId, legacyPool: pool };
}

const isInt = (v: unknown, min: number, max: number): v is number =>
  Number.isInteger(v) && (v as number) >= min && (v as number) <= max;

/**
 * A new stored link's settings from the console's request: the defaults filled in (size 1,
 * min size = size, no delay, 5 min max wait, no password) and every value checked, so the
 * runner gets only a valid link. Older callers send only the pool: they get a name.
 */
export function linkSettings(body: unknown): { link: NewLink } | { error: string } {
  if (typeof body !== 'object' || body === null || Array.isArray(body))
    return { error: 'expected a JSON object' };
  const b = body as Record<string, unknown>;
  const name =
    b.name === undefined ? 'Untitled link' : typeof b.name === 'string' ? b.name.trim() : '';
  const { pool, size = 1, delay_s = 0, max_wait_s = 300, password = '' } = b;
  const min_size = b.min_size ?? size;
  if (!name) return { error: 'name is required' };
  if (!isValidPool(pool)) return { error: 'pool must be a non-empty array of config scope names' };
  if (!isInt(size, 1, 8)) return { error: 'room size must be a whole number from 1 to 8' };
  if (!isInt(min_size, 1, size))
    return { error: 'min size must be a whole number from 1 to the room size' };
  if (!isInt(delay_s, 0, 300)) return { error: 'start delay must be 0 to 300 seconds' };
  if (!isInt(max_wait_s, 60, 1800)) return { error: 'max wait must be 1 to 30 minutes' };
  if (typeof password !== 'string') return { error: 'password must be text' };
  return {
    link: { name, pool, size, min_size, delay_s, max_wait_s, ...(password && { password }) },
  };
}

/** Unbiased uniform pick (crypto.randomInt, not Math.random). */
export function pickUniform<T>(items: T[]): T {
  return items[randomInt(items.length)];
}

/** Unique room name matching ROOM_NAME_PATTERN (^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$). */
export function generateStartRoomName(): string {
  return `link-${Date.now().toString(36)}-${randomUUID().replace(/-/g, '').slice(0, 8)}`;
}
