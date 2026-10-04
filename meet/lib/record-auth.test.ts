import { describe, it, expect } from 'vitest';
import { NextRequest } from 'next/server';
import { SignJWT } from 'jose';
import { mayControlRecording } from './record-auth';

// Who may start or stop a room's recording (F10): a logged-in console session, only. Not a
// participant: anyone who names a room can get its token (open join), and a study's
// recording must not be stopped from inside the room.
const SECRET = 'test-secret-test-secret-test-secret';
const key = new TextEncoder().encode(SECRET);
const now = () => Math.floor(Date.now() / 1000);

const sign = (claims: Record<string, unknown>, exp: number, secret = key) =>
  new SignJWT(claims).setProtectedHeader({ alg: 'HS256' }).setExpirationTime(exp).sign(secret);
const participant = (room: string, exp = now() + 300) => sign({ sub: 'p1', video: { room, roomJoin: true } }, exp);

function req({ bearer, cookie }: { bearer?: string; cookie?: string }) {
  const headers: Record<string, string> = {};
  if (bearer) headers['authorization'] = `Bearer ${bearer}`;
  if (cookie) headers['cookie'] = `console-session=${cookie}`;
  return new NextRequest('http://localhost/api/record/start?roomName=study-1', { headers });
}

describe('mayControlRecording', () => {
  it('lets a logged-in console session control any room', async () => {
    const console_ = await sign({ sub: 'console' }, now() + 3600);
    expect(await mayControlRecording(req({ cookie: console_ }), SECRET)).toBe(true);
  });

  it("refuses a participant, even with a valid token for that room: anyone who names a room can get one", async () => {
    expect(await mayControlRecording(req({ bearer: await participant('study-1') }), SECRET)).toBe(false);
  });

  it('refuses anyone with nothing, a forged session, or a non-console cookie', async () => {
    expect(await mayControlRecording(req({}), SECRET)).toBe(false);
    const forged = await sign({ sub: 'console' }, now() + 3600, new TextEncoder().encode('other-secret-other-secret-123456'));
    expect(await mayControlRecording(req({ cookie: forged }), SECRET)).toBe(false);
    expect(await mayControlRecording(req({ cookie: await participant('study-1') }), SECRET)).toBe(false);
  });

  it('refuses an expired console session', async () => {
    expect(await mayControlRecording(req({ cookie: await sign({ sub: 'console' }, now() - 60) }), SECRET)).toBe(false);
  });
});
