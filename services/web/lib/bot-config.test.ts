import { describe, it, expect } from 'vitest';
import { saveConfirmation, scopeOf } from './bot-config';

// Bot settings' "Applies to" box and Save: the scope saved is the one typed, and saving the
// global settings (every live study at once) asks first.
describe('scopeOf', () => {
  it('is what was typed, without stray spaces', () => {
    expect(scopeOf('  study-1 ')).toBe('study-1');
  });
  it('is global when the box is empty', () => {
    expect(scopeOf('   ')).toBe('global');
  });
});

describe('saveConfirmation', () => {
  it('asks before changing the global settings', () => {
    expect(saveConfirmation('global')).toBe(
      'This changes the bot for every room without its own settings, including studies running now. Save?',
    );
  });
  it("does not ask for one room's settings", () => {
    expect(saveConfirmation('study-1')).toBeNull();
  });
});
