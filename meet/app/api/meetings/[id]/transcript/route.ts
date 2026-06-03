import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';

export const dynamic = 'force-dynamic';

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const { botRunnerUrl, botRunnerSecret } = getServerConfig();
  if (!botRunnerUrl) {
    return NextResponse.json({ error: 'BOT_RUNNER_URL is not configured' }, { status: 500, headers: noStoreHeaders() });
  }

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (botRunnerSecret) headers['Authorization'] = `Bearer ${botRunnerSecret}`;

  try {
    const res = await fetch(`${botRunnerUrl}conversations/${encodeURIComponent(id)}/transcript`, {
      method: 'POST',
      headers,
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status, headers: noStoreHeaders() });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to queue transcript';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
