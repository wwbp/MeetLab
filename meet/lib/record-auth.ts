import type { NextRequest } from 'next/server';
import { jwtVerify } from 'jose';
import { COOKIE_NAME } from './console/auth';

/** Who may start or stop a room's recording (F10): a logged-in console session, only.
 *  Not a participant: anyone who names a room can get a token for it (rooms are open to
 *  join by link), and a study's recording must not be stopped from inside the room.
 *  Researchers record with Bot Config's auto-record. */
export async function mayControlRecording(req: NextRequest, secret: string): Promise<boolean> {
  const session = req.cookies.get(COOKIE_NAME)?.value;
  if (!session) return false;
  try {
    const { payload } = await jwtVerify(session, new TextEncoder().encode(secret));
    // Other JWTs signed with the same secret exist (participant tokens): only a console one.
    return payload.sub === 'console';
  } catch {
    return false;
  }
}
