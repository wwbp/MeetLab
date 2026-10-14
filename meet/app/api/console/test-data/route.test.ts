// The live tests clear their own data after each run (user, 2026-10-14): the console forwards
// to the runner, which deletes only rooms named like the tests' (runner.TEST_ROOMS).
import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe('DELETE /api/console/test-data', () => {
  it("forwards to the runner's /test-data with the runner secret and returns its counts", async () => {
    vi.stubEnv('BOT_RUNNER_URL', 'http://agent-runner:7860/');
    vi.stubEnv('BOT_RUNNER_SECRET', 's3cret');
    const calls: Array<[string, RequestInit]> = [];
    vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
      calls.push([url, init]);
      return Response.json({ conversations: 3, files: 5 });
    });
    const { DELETE } = await import('./route');
    const res = await DELETE();
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe('http://agent-runner:7860/test-data');
    expect(calls[0][1].method).toBe('DELETE');
    expect((calls[0][1].headers as Record<string, string>).Authorization).toBe('Bearer s3cret');
    expect(await res.json()).toEqual({ conversations: 3, files: 5 });
  });
});
