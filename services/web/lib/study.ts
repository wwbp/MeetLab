/**
 * Prolific IDs and completion codes — the two identifiers a paid study runs on.
 *
 * Prolific sends a participant here with `?PROLIFIC_PID=...` and pays out by
 * matching that ID to a completed survey. Nothing else in the app knows who a
 * participant is: LiveKit sees a display name they typed.
 *
 * This half is pure so it can run in the browser: the pre-join form validates the
 * ID before anyone joins. The completion code needs the signing secret and lives
 * in ./completion-code, server-side only.
 */

const PROLIFIC_ID = /^[0-9a-f]{24}$/;

/** The ID in canonical form, or null if it is not a Prolific ID. */
export function normalizeProlificId(raw: string | null | undefined): string | null {
  const candidate = (raw ?? '').trim().toLowerCase();
  return PROLIFIC_ID.test(candidate) ? candidate : null;
}

/**
 * What to prefill the join form with. Deliberately returns the raw value rather
 * than only valid ones: showing a malformed ID lets the participant correct it,
 * whereas silently blanking it looks like the link was fine.
 */
export function prolificIdFromParams(params: URLSearchParams): string {
  return (params.get('PROLIFIC_PID') ?? params.get('prolific_pid') ?? '').trim();
}

