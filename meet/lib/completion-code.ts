import { createHmac } from 'node:crypto';

/**
 * The string a participant copies into the survey to show they sat the session.
 *
 * Derived rather than stored: no table to keep in sync, no endpoint to call, and
 * nothing lost when meet restarts (all of its state is in-memory). Server-side
 * only — it needs LIVEKIT_API_SECRET, and a code the browser could compute would
 * be a code any participant could forge.
 */

/**
 * Crockford-style alphabet: no O/0 or I/1, the pairs people get wrong when
 * retyping a code into a survey field. A mistyped code is indistinguishable from
 * a participant who never showed up.
 */
const ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';
const CODE_LENGTH = 8;

/**
 * The code the participant copies into the survey. Per room and per participant,
 * so a code cannot be passed between people or reused from an earlier session.
 * Recomputable by whoever holds the secret, which is how a researcher checks one.
 */
export function completionCode(roomName: string, participantId: string, secret: string): string {
  if (!secret) throw new Error('LIVEKIT_API_SECRET is not set');
  const digest = createHmac('sha256', secret).update(`${roomName}:${participantId}`).digest();
  let code = '';
  for (let i = 0; i < CODE_LENGTH; i++) {
    code += ALPHABET[digest[i] % ALPHABET.length];
  }
  return code;
}
