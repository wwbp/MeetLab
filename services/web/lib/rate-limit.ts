// In-memory fixed-window rate limiter.
// Stored on globalThis so state persists across Next.js hot-reloads and
// across route-handler invocations in the same Node.js process (standalone Docker).
// Not suitable for multi-instance deployments — use Redis there instead.

const STORE_KEY = '__meetlab_rate_limit__';
const MAX_STORE_SIZE = 20_000;

type WindowEntry = { count: number; windowStart: number };

function getStore(): Map<string, WindowEntry> {
  const g = globalThis as Record<string, unknown>;
  if (!(STORE_KEY in g)) {
    g[STORE_KEY] = new Map<string, WindowEntry>();
  }
  return g[STORE_KEY] as Map<string, WindowEntry>;
}

/**
 * Fixed-window rate limit check.
 *
 * @param key       Discriminator (e.g. IP address or `ip:route`).
 * @param max       Maximum requests allowed in the window.
 * @param windowMs  Window duration in milliseconds.
 * @returns `{ allowed, retryAfterMs }` — callers return 429 when !allowed.
 */
export function checkRateLimit(
  key: string,
  max: number,
  windowMs: number,
): { allowed: boolean; retryAfterMs: number } {
  const store = getStore();
  const now = Date.now();

  // Lazy eviction: purge expired entries when the store grows large.
  if (store.size >= MAX_STORE_SIZE) {
    for (const [k, v] of store) {
      if (now - v.windowStart >= windowMs) store.delete(k);
    }
  }

  const entry = store.get(key);

  if (!entry || now - entry.windowStart >= windowMs) {
    store.set(key, { count: 1, windowStart: now });
    return { allowed: true, retryAfterMs: 0 };
  }

  if (entry.count >= max) {
    return { allowed: false, retryAfterMs: windowMs - (now - entry.windowStart) };
  }

  entry.count += 1;
  return { allowed: true, retryAfterMs: 0 };
}

/** Extracts the best available client IP from a Next.js request. */
export function getClientIp(request: Request): string {
  const headers = new Headers(request.headers);
  const forwarded = headers.get('x-forwarded-for');
  if (forwarded) return forwarded.split(',')[0].trim();
  return headers.get('x-real-ip') ?? 'unknown';
}
