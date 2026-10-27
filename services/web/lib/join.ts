/**
 * What a participant sees between clicking a study link and being in the call,
 * and when the call ends. Pure so it can be tested; the pages render the copy.
 */
import { DisconnectReason } from 'livekit-client';
import type { ConnectionDetails } from './types';

export const JOIN_FAILED = "Couldn't join right now. Please wait a minute and try again.";

/** Camera off: this is a voice study. They can still turn it on before joining. */
export const PRE_JOIN_DEFAULTS = { username: '', videoEnabled: false, audioEnabled: true };

/**
 * `target` with the current page's query (`?PROLIFIC_PID=...`) merged in, so the
 * start link's redirect does not lose the ID Prolific handed over.
 */
export function carryQuery(target: string, search: string): string {
  const url = new URL(target, 'http://relative.invalid');
  new URLSearchParams(search).forEach((value, key) => url.searchParams.set(key, value));
  return url.origin === 'http://relative.invalid'
    ? url.pathname + url.search + url.hash
    : url.href;
}

/** The connection-details body; errors are plain text, so check before parsing. */
export async function readConnectionDetails(resp: Response): Promise<ConnectionDetails> {
  if (!resp.ok) throw new Error(JOIN_FAILED);
  return resp.json();
}

/**
 * Whether a disconnect ends the session (completion screen) or was a dropped
 * connection the participant should rejoin from. Leave, and the meeting being
 * closed from the console, end it.
 */
export function endsStudy(reason?: DisconnectReason): boolean {
  return reason === DisconnectReason.CLIENT_INITIATED || reason === DisconnectReason.ROOM_DELETED;
}

/** Plain copy for a camera/microphone or connection error. */
export function mediaErrorMessage(error: Error): string {
  if (error.name === 'NotAllowedError' || /permission denied/i.test(error.message)) {
    return 'Your browser blocked the microphone. Please allow the microphone using the icon at the left of the address bar, then reload this page.';
  }
  return 'Something went wrong with your call. Please reload this page and try again.';
}

/**
 * The notice shown in the call while no bot (`bot_*` identity) is present: it is
 * starting (up to ~3 minutes cold) or, if one was here before, being replaced.
 */
export function botNotice(identities: string[], botSeenBefore: boolean): string | null {
  if (identities.some((id) => id.startsWith('bot_'))) return null;
  return botSeenBefore
    ? 'Your conversation partner is reconnecting…'
    : 'Your conversation partner is joining. This can take up to 3 minutes. Please stay on this page.';
}
