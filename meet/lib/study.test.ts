/**
 * The two things a paid study needs from the join and leave screens.
 *
 * A Prolific study hands the participant off with `?PROLIFIC_PID=...` in the URL
 * and pays out by matching that ID. The parameter goes missing often enough —
 * bookmarks, refreshes, extensions that strip query strings — that the field has
 * to be editable rather than purely derived, which means it also has to be
 * validated: a mistyped ID is an unpayable session nobody notices for weeks.
 *
 * The completion code is what the participant pastes into the survey to show they
 * actually sat the session. It is derived, not stored, so there is no table to
 * keep in sync and no endpoint to call — but it has to be unguessable, or it
 * proves nothing.
 */
import { describe, expect, it } from 'vitest';
import { completionCode } from './completion-code';
import { normalizeProlificId, prolificIdFromParams } from './study';

const SECRET = 'test-secret-not-a-real-key';

describe('normalizeProlificId', () => {
  it('accepts a well-formed ID', () => {
    expect(normalizeProlificId('5f2a91b3c4d5e6f708192a3b')).toBe('5f2a91b3c4d5e6f708192a3b');
  });

  it('forgives whitespace from a paste', () => {
    expect(normalizeProlificId('  5f2a91b3c4d5e6f708192a3b ')).toBe('5f2a91b3c4d5e6f708192a3b');
  });

  it('normalizes case so one person is not two rows', () => {
    expect(normalizeProlificId('5F2A91B3C4D5E6F708192A3B')).toBe('5f2a91b3c4d5e6f708192a3b');
  });

  it('rejects the wrong length', () => {
    expect(normalizeProlificId('5f2a91b3')).toBeNull();
    expect(normalizeProlificId('5f2a91b3c4d5e6f708192a3bff')).toBeNull();
  });

  it('rejects non-hex, which is what a typo looks like', () => {
    expect(normalizeProlificId('zzzz91b3c4d5e6f708192a3b')).toBeNull();
  });

  it('rejects nothing at all', () => {
    expect(normalizeProlificId('')).toBeNull();
    expect(normalizeProlificId(null)).toBeNull();
    expect(normalizeProlificId(undefined)).toBeNull();
  });
});

describe('prolificIdFromParams', () => {
  it('reads the parameter Prolific actually sends', () => {
    const p = new URLSearchParams('PROLIFIC_PID=5f2a91b3c4d5e6f708192a3b&STUDY_ID=x');
    expect(prolificIdFromParams(p)).toBe('5f2a91b3c4d5e6f708192a3b');
  });

  it('tolerates the lowercase spelling researchers hand-write in links', () => {
    const p = new URLSearchParams('prolific_pid=5f2a91b3c4d5e6f708192a3b');
    expect(prolificIdFromParams(p)).toBe('5f2a91b3c4d5e6f708192a3b');
  });

  it('prefills nothing when the parameter is absent', () => {
    expect(prolificIdFromParams(new URLSearchParams(''))).toBe('');
  });

  it('prefills the raw value even when it is malformed, so it can be corrected', () => {
    // Blanking a wrong value hides the problem; showing it lets the participant
    // see what arrived and fix it before joining.
    const p = new URLSearchParams('PROLIFIC_PID=not-an-id');
    expect(prolificIdFromParams(p)).toBe('not-an-id');
  });
});

describe('completionCode', () => {
  const room = 'study-room-1';
  const pid = '5f2a91b3c4d5e6f708192a3b';

  it('is stable for the same participant in the same room', () => {
    expect(completionCode(room, pid, SECRET)).toBe(completionCode(room, pid, SECRET));
  });

  it('differs per participant, so one code cannot be shared around', () => {
    expect(completionCode(room, pid, SECRET)).not.toBe(
      completionCode(room, 'a1b2c3d4e5f60718293a4b5c', SECRET),
    );
  });

  it('differs per room, so a code from a previous session does not pass', () => {
    expect(completionCode(room, pid, SECRET)).not.toBe(completionCode('other-room', pid, SECRET));
  });

  it('cannot be produced without the secret', () => {
    expect(completionCode(room, pid, SECRET)).not.toBe(completionCode(room, pid, 'other-secret'));
  });

  it('is short enough to retype and long enough not to guess', () => {
    const code = completionCode(room, pid, SECRET);
    expect(code).toHaveLength(8);
    expect(code).toMatch(/^[0-9A-Z]{8}$/);
  });

  it('avoids characters that are misread when retyped', () => {
    // O/0 and I/1 are the classic transcription errors, and a wrong code looks
    // exactly like a participant who skipped the session.
    const codes = Array.from({ length: 200 }, (_, i) => completionCode(`room-${i}`, pid, SECRET));
    expect(codes.join('')).not.toMatch(/[O0I1]/);
  });

  it('refuses to invent a code without a secret to sign it with', () => {
    // Falling back to an unsigned code would hand out something anyone can forge.
    expect(() => completionCode(room, pid, '')).toThrow();
  });
});
