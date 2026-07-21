import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';

export const dynamic = 'force-dynamic';

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const { botRunnerUrl, botRunnerSecret } = getServerConfig();
  if (!botRunnerUrl) {
    return NextResponse.json({ error: 'BOT_RUNNER_URL is not configured' }, { status: 500, headers: noStoreHeaders() });
  }

  const headers: Record<string, string> = {};
  if (botRunnerSecret) headers['Authorization'] = `Bearer ${botRunnerSecret}`;

  try {
    const res = await fetch(
      `${botRunnerUrl}conversations/${encodeURIComponent(id)}/audio-tracks/download`,
      { headers },
    );

    if (!res.ok) {
      const data = await res.json().catch(() => ({ error: 'download failed' }));
      return NextResponse.json(data, { status: res.status, headers: noStoreHeaders() });
    }

    return new NextResponse(res.body, {
      status: 200,
      headers: {
        'Content-Type': 'application/zip',
        'Content-Disposition': res.headers.get('content-disposition') ?? 'attachment',
        'Cache-Control': 'no-store',
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Download failed';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
