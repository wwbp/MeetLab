import { type NextRequest } from 'next/server';
import { proxyToSqlAdmin } from '@/lib/console/db-proxy';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  return proxyToSqlAdmin(request);
}

export async function POST(request: NextRequest) {
  return proxyToSqlAdmin(request);
}

export async function DELETE(request: NextRequest) {
  return proxyToSqlAdmin(request);
}
