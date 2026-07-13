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
    if (res.ok && Array.isArray(data?.configs)) {
      // Every start-link click materializes the picked config as scope=link-<room>
      // (that's how the bot resolves it). Those are per-room copies, not reusable
      // presets — hide them from the picker. They remain visible in DB Admin.
      data.configs = data.configs.filter(
        (c: { scope?: string }) => !(typeof c.scope === 'string' && c.scope.startsWith('link-'))
      );
    }
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to list configs';
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
