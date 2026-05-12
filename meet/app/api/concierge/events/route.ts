import { NextResponse } from 'next/server';
import { listConciergeEvents } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const limitParam = searchParams.get('limit');
  const limit = limitParam ? Number.parseInt(limitParam, 10) : 50;

  return NextResponse.json(
    { events: listConciergeEvents(limit) },
    {
      headers: noStoreHeaders(),
    }
  );
}
