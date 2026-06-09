import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { checkRateLimit, getClientIp } from './rate-limit';

// Isolate globalThis store between test suites by wiping it before each test.
const STORE_KEY = '__meetlab_rate_limit__';
beforeEach(() => {
  delete (globalThis as Record<string, unknown>)[STORE_KEY];
});
afterEach(() => {
  vi.useRealTimers();
});

describe('checkRateLimit', () => {
  it('allows the first request', () => {
    const { allowed } = checkRateLimit('ip-a', 5, 60_000);
    expect(allowed).toBe(true);
  });

  it('allows requests up to the max', () => {
    for (let i = 0; i < 5; i++) {
      expect(checkRateLimit('ip-b', 5, 60_000).allowed).toBe(true);
    }
  });

  it('blocks the request that exceeds the max', () => {
    for (let i = 0; i < 5; i++) checkRateLimit('ip-c', 5, 60_000);
    const { allowed, retryAfterMs } = checkRateLimit('ip-c', 5, 60_000);
    expect(allowed).toBe(false);
    expect(retryAfterMs).toBeGreaterThan(0);
  });

  it('resets the window after the window duration elapses', () => {
    vi.useFakeTimers();

    for (let i = 0; i < 5; i++) checkRateLimit('ip-d', 5, 60_000);
    expect(checkRateLimit('ip-d', 5, 60_000).allowed).toBe(false);

    vi.advanceTimersByTime(60_001);
    expect(checkRateLimit('ip-d', 5, 60_000).allowed).toBe(true);
  });

  it('tracks different keys independently', () => {
    for (let i = 0; i < 5; i++) checkRateLimit('ip-e', 5, 60_000);
    expect(checkRateLimit('ip-e', 5, 60_000).allowed).toBe(false);
    // Different key is unaffected
    expect(checkRateLimit('ip-f', 5, 60_000).allowed).toBe(true);
  });

  it('retryAfterMs decreases over time', () => {
    vi.useFakeTimers();

    for (let i = 0; i < 3; i++) checkRateLimit('ip-g', 3, 60_000);

    const { retryAfterMs: r1 } = checkRateLimit('ip-g', 3, 60_000);
    expect(r1).toBeGreaterThan(59_000);

    vi.advanceTimersByTime(10_000);

    const { retryAfterMs: r2 } = checkRateLimit('ip-g', 3, 60_000);
    expect(r2).toBeLessThan(r1);
  });

  it('retryAfterMs is 0 when allowed', () => {
    const { retryAfterMs } = checkRateLimit('ip-h', 10, 60_000);
    expect(retryAfterMs).toBe(0);
  });
});

describe('getClientIp', () => {
  it('extracts first IP from x-forwarded-for chain', () => {
    const req = new Request('http://localhost/', {
      headers: { 'x-forwarded-for': '1.2.3.4, 10.0.0.1, 10.0.0.2' },
    });
    expect(getClientIp(req)).toBe('1.2.3.4');
  });

  it('falls back to x-real-ip', () => {
    const req = new Request('http://localhost/', {
      headers: { 'x-real-ip': '5.6.7.8' },
    });
    expect(getClientIp(req)).toBe('5.6.7.8');
  });

  it('returns "unknown" when no IP header is present', () => {
    const req = new Request('http://localhost/');
    expect(getClientIp(req)).toBe('unknown');
  });

  it('x-forwarded-for takes precedence over x-real-ip', () => {
    const req = new Request('http://localhost/', {
      headers: {
        'x-forwarded-for': '9.9.9.9',
        'x-real-ip': '1.1.1.1',
      },
    });
    expect(getClientIp(req)).toBe('9.9.9.9');
  });
});
