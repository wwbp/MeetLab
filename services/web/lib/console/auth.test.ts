import { describe, it, expect, afterEach, vi } from 'vitest';
import { jwtVerify } from 'jose';
import { signSession } from './auth';

// The console session has its own key, not LIVEKIT_API_SECRET (held by every bot task
// and by LiveKit Cloud).
const CONSOLE = 'console-session-secret-console-session';
const LIVEKIT = 'livekit-secret-livekit-secret-livekit';
const key = (s: string) => new TextEncoder().encode(s);

describe('signSession', () => {
  afterEach(() => vi.unstubAllEnvs());

  it('signs with CONSOLE_SESSION_SECRET', async () => {
    vi.stubEnv('CONSOLE_SESSION_SECRET', CONSOLE);
    vi.stubEnv('LIVEKIT_API_SECRET', LIVEKIT);
    const token = await signSession();
    await expect(jwtVerify(token, key(CONSOLE))).resolves.toBeTruthy();
    await expect(jwtVerify(token, key(LIVEKIT))).rejects.toThrow();
  });

  it('throws when CONSOLE_SESSION_SECRET is unset, rather than fall back to another key', async () => {
    vi.stubEnv('CONSOLE_SESSION_SECRET', '');
    vi.stubEnv('LIVEKIT_API_SECRET', LIVEKIT);
    await expect(signSession()).rejects.toThrow('CONSOLE_SESSION_SECRET');
  });
});
