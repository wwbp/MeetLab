import { NextResponse } from 'next/server';
import { getServerConfig, requireEnv } from '@/lib/config/server';

export const dynamic = 'force-dynamic';

function botRunnerConfigUrl(base: string, scope?: string | null): string {
  const normalized = base.endsWith('/') ? base : `${base}/`;
  const url = `${normalized}config`;
  return scope && scope !== 'global' ? `${url}?room=${encodeURIComponent(scope)}` : url;
}

function runnerHeaders(secret?: string): HeadersInit {
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  if (secret) headers['Authorization'] = `Bearer ${secret}`;
  return headers;
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const scope = searchParams.get('scope');
  const config = getServerConfig();
  const botRunnerUrl = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL');

  try {
    const res = await fetch(botRunnerConfigUrl(botRunnerUrl, scope), {
      headers: runnerHeaders(config.botRunnerSecret),
      cache: 'no-store',
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to fetch config';
    return NextResponse.json({ error: message }, { status: 502 });
  }
}

export async function PUT(request: Request) {
  const config = getServerConfig();
  const botRunnerUrl = requireEnv(config.botRunnerUrl, 'BOT_RUNNER_URL');
  const body = await request.json().catch(() => ({}));

  try {
    const normalized = botRunnerUrl.endsWith('/') ? botRunnerUrl : `${botRunnerUrl}/`;
    const res = await fetch(`${normalized}config`, {
      method: 'PUT',
      headers: runnerHeaders(config.botRunnerSecret),
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to update config';
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
