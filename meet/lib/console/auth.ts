import { SignJWT, jwtVerify } from 'jose';

export const COOKIE_NAME = 'console-session';
export const SESSION_TTL_SECONDS = 8 * 60 * 60; // 8 hours

function signingKey(): Uint8Array {
  const secret = process.env.LIVEKIT_API_SECRET;
  if (!secret) throw new Error('LIVEKIT_API_SECRET is not set');
  return new TextEncoder().encode(secret);
}

export async function signSession(): Promise<string> {
  return new SignJWT({ sub: 'console' })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime(`${SESSION_TTL_SECONDS}s`)
    .sign(signingKey());
}

export async function verifySession(token: string): Promise<boolean> {
  try {
    await jwtVerify(token, signingKey());
    return true;
  } catch {
    return false;
  }
}
