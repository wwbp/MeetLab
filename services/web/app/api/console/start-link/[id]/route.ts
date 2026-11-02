import { NextResponse } from 'next/server';
import { setLinkActive } from '@/lib/matchmaking-client';

export const dynamic = 'force-dynamic';

/** Stop or start a link: { active }. Running meetings and waiting tickets carry on.
 *  Console-session guarded via middleware. */
export async function PATCH(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const active = (await request.json().catch(() => null))?.active;
  if (typeof active !== 'boolean')
    return NextResponse.json({ error: 'active must be true or false' }, { status: 400 });
  const { status, body } = await setLinkActive(id, active);
  return NextResponse.json(body, { status });
}
