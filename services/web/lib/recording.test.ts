import { describe, expect, it } from 'vitest';
import { cardLabel, gridColumns, initials } from './recording';

describe('gridColumns', () => {
  it('fits everyone on the recording in the fewest rows that keep cards large', () => {
    expect([1, 2, 3, 4, 5, 6, 7, 9, 10].map(gridColumns)).toEqual([1, 2, 2, 2, 3, 3, 3, 3, 4]);
  });

  it('an empty room still has a valid grid', () => {
    expect(gridColumns(0)).toBe(1);
  });
});

describe('cardLabel', () => {
  it("is the person's name", () => {
    expect(cardLabel({ name: 'Ana', identity: 'ana__1a2b' })).toBe('Ana');
  });

  it('without a name, the identity before its suffix', () => {
    expect(cardLabel({ name: '', identity: 'ana__1a2b' })).toBe('ana');
    expect(cardLabel({ identity: 'ana' })).toBe('ana');
  });

  it('the bot is the Assistant', () => {
    expect(cardLabel({ name: '', identity: 'bot_room_1' })).toBe('Assistant');
  });
});

describe('initials', () => {
  it('one or two letters, upper case', () => {
    expect(initials('Ana')).toBe('A');
    expect(initials('ana maria lopez')).toBe('AM');
    expect(initials('  ')).toBe('?');
  });
});
