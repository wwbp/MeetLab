import { NextResponse } from 'next/server';
import { getServerConfig, requireEnv } from '@/lib/config/server';
import { noteRouteError } from '@/lib/concierge/event-log';

export const dynamic = 'force-dynamic';

// The live tests clear their own data after each run (infra/live-tests.sh). The runner
// deletes only rooms named like the tests' (runner.TEST_ROOMS); console login required
// (middleware.ts).
export async function DELETE() {
  const config = getServerConfig();
  const base = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL');
  const headers: Record<string, string> = {};
  if (config.botRunnerSecret) headers.Authorization = `Bearer ${config.botRunnerSecret}`;
  try {
    const res = await fetch(`${base.endsWith('/') ? base : `${base}/`}test-data`, { method: 'DELETE', headers });
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (err) {
    noteRouteError('DELETE /api/console/test-data', err);
    return NextResponse.json({ error: err instanceof Error ? err.message : 'Failed to purge test data' }, { status: 502 });
  }
}
