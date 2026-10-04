import { describe, it, expect } from 'vitest';
import { completionCode } from './completion-code';

// A fixed example, pinned here and in agent-runner/tests/test_completion_code.py: the code a
// participant pastes into the survey must be recomputable from the Prolific export, by
// meet and by anyone checking it (docs/study-support.md).
describe('completionCode', () => {
  it('is the pinned code for a known room, Prolific ID and secret', () => {
    expect(completionCode('study-7', '5f2a91b3c4d5e6f708192a3b', 'test-secret')).toBe('Y7MH4UK6');
  });

  it('differs by room and by participant', () => {
    const a = completionCode('study-7', '5f2a91b3c4d5e6f708192a3b', 'test-secret');
    expect(completionCode('study-8', '5f2a91b3c4d5e6f708192a3b', 'test-secret')).not.toBe(a);
    expect(completionCode('study-7', '5f2a91b3c4d5e6f708192a3c', 'test-secret')).not.toBe(a);
  });

  it('is 8 characters a person can read without confusing 0/O or 1/I', () => {
    expect(completionCode('r', 'p', 's')).toMatch(/^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}$/);
  });
});
