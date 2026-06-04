import { NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';

export const dynamic = 'force-dynamic';

export async function POST() {
  const { botRunnerUrl, botRunnerSecret } = getServerConfig();
  if (!botRunnerUrl) {
    return NextResponse.json({ error: 'BOT_RUNNER_URL is not configured' }, { status: 500, headers: noStoreHeaders() });
  }

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (botRunnerSecret) headers['Authorization'] = `Bearer ${botRunnerSecret}`;

  try {
    const res = await fetch(`${botRunnerUrl}recordings/reconcile`, { method: 'POST', headers });
    const text = await res.text();
    let data: unknown;
    try { data = JSON.parse(text); } catch { data = { raw: text }; }
    return NextResponse.json(data, { status: res.status, headers: noStoreHeaders() });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Reconcile failed';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
