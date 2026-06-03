import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';
import { noStoreHeaders } from '@/lib/concierge/http-utils';

export const dynamic = 'force-dynamic';

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string; fileId: string }> },
) {
  const { fileId } = await params;
  const { botRunnerUrl, botRunnerSecret } = getServerConfig();
  if (!botRunnerUrl) {
    return NextResponse.json({ error: 'BOT_RUNNER_URL is not configured' }, { status: 500, headers: noStoreHeaders() });
  }

  const headers: Record<string, string> = {};
  if (botRunnerSecret) headers['Authorization'] = `Bearer ${botRunnerSecret}`;

  try {
    const res = await fetch(
      `${botRunnerUrl}media-files/${encodeURIComponent(fileId)}/download`,
      { headers, redirect: 'manual' },
    );

    // S3 presigned redirect — pass through to client
    if (res.status === 302) {
      const location = res.headers.get('location');
      if (location) {
        return NextResponse.redirect(location, { status: 302 });
      }
    }

    if (!res.ok) {
      const data = await res.json().catch(() => ({ error: 'download failed' }));
      return NextResponse.json(data, { status: res.status, headers: noStoreHeaders() });
    }

    // Local file — stream through
    const contentType = res.headers.get('content-type') ?? 'application/octet-stream';
    const contentDisposition = res.headers.get('content-disposition') ?? 'attachment';
    const body = res.body;
    return new NextResponse(body, {
      status: 200,
      headers: {
        'Content-Type': contentType,
        'Content-Disposition': contentDisposition,
        'Cache-Control': 'no-store',
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Download failed';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}
