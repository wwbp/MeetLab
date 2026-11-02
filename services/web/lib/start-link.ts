import { createHash, randomInt, randomUUID } from 'node:crypto';
import { SignJWT, jwtVerify } from 'jose';

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

/** Unbiased uniform pick (crypto.randomInt, not Math.random). */
export function pickUniform<T>(items: T[]): T {
  return items[randomInt(items.length)];
}

/** Unique room name matching ROOM_NAME_PATTERN (^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$). */
export function generateStartRoomName(): string {
  return `link-${Date.now().toString(36)}-${randomUUID().replace(/-/g, '').slice(0, 8)}`;
}
