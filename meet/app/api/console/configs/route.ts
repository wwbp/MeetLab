import { NextResponse } from 'next/server';
import { getServerConfig, requireEnv } from '@/lib/config/server';

export const dynamic = 'force-dynamic';

/** List all bot-config scopes (proxy to agent-runner GET /configs) — feeds the
 *  start-link pool picker. Console-session guarded via middleware. */
export async function GET() {
  const config = getServerConfig();
  const botRunnerUrl = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL');
  const normalized = botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`;

  const headers: HeadersInit = {};
  if (config.botRunnerSecret) headers['Authorization'] = `Bearer ${config.botRunnerSecret}`;

  try {
    const res = await fetch(`${normalized}configs`, { headers, cache: 'no-store' });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to list configs';
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
