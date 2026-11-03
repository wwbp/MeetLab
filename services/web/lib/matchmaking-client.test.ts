import fs from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createLink,
  createTicket,
  listLinks,
  pollTicket,
  publicLink,
  readyTicket,
  setLinkActive,
} from './matchmaking-client';

// The runner's contract: one file per request/reply (the runner's tests read the same files).
// Repo root's tests/fixtures; in the meet container it is mounted at /tests/fixtures.
const DIR = path.join(__dirname, '..', '..', '..', 'tests', 'fixtures', 'matchmaking');

type Fixture = {
  endpoint: string;
  request: { method: string; path: string; body?: Record<string, unknown> };
  response: { status: number; body: unknown };
};

const fixtures: [string, Fixture][] = fs
  .readdirSync(DIR)
  .filter((f) => f.endsWith('.json'))
  .map((f) => [f, JSON.parse(fs.readFileSync(path.join(DIR, f), 'utf8'))]);

const idOf = (f: Fixture) => decodeURIComponent(f.request.path.split('/')[2]);

const CALLS: Record<string, (f: Fixture) => Promise<unknown>> = {
  'POST /links': (f) => createLink(f.request.body as Parameters<typeof createLink>[0]),
  'GET /links': () => listLinks(),
  'PATCH /links/{id}': (f) => setLinkActive(idOf(f), f.request.body?.active as boolean),
  'GET /links/{id}/public': (f) => publicLink(idOf(f)),
  'POST /tickets': (f) => createTicket(f.request.body as Parameters<typeof createTicket>[0]),
  'POST /tickets/{id}/poll': (f) => pollTicket(idOf(f)),
  'POST /tickets/{id}/ready': (f) => readyTicket(idOf(f)),
};

describe('matchmaking client', () => {
  beforeEach(() => {
    vi.stubEnv('BOT_RUNNER_URL', 'http://runner:7860');
    vi.stubEnv('BOT_RUNNER_SECRET', 's3cret');
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('has a call for every endpoint in the fixtures', () => {
    expect(fixtures.length).toBeGreaterThan(10);
    expect(new Set(fixtures.map(([, f]) => f.endpoint))).toEqual(new Set(Object.keys(CALLS)));
  });

  it.each(fixtures)('%s: sends the request and returns the reply', async (_name, f) => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(f.response.body), { status: f.response.status }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const reply = await CALLS[f.endpoint](f);

    expect(reply).toEqual({ status: f.response.status, body: f.response.body });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`http://runner:7860${f.request.path}`);
    expect(init.method).toBe(f.request.method);
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer s3cret');
    expect(init.body === undefined ? undefined : JSON.parse(init.body as string)).toEqual(
      f.request.body,
    );
  });

  it('reports an unreachable runner as a 502, not a throw', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new Error('ECONNREFUSED'))),
    );
    expect(await pollTicket('6f1c2b3a-4d5e-4f60-8a7b-9c0d1e2f3a4b')).toEqual({
      status: 502,
      body: { error: 'runner unreachable: ECONNREFUSED' },
    });
  });
});
