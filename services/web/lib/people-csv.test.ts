import { describe, it, expect } from 'vitest';
import { peopleCsv } from './people-csv';

// One meeting's people as a spreadsheet: who to pay, matched on Prolific ID (docs/study-support.md).
describe('peopleCsv', () => {
  const meeting = { room_name: 'study-1', started_at: '2026-10-27T14:00:00+00:00' };

  it('has a header row and one row per person, in the order they first spoke', () => {
    const csv = peopleCsv({
      ...meeting,
      speakers: [
        { display_name: 'Ana', prolific_id: '5f2a91b3c4d5e6f708192a3b', prolific_id_invalid: null, first_spoke_at: '2026-10-27T14:01:00+00:00' },
        { display_name: 'Ben', prolific_id: null, prolific_id_invalid: 'oops', first_spoke_at: '2026-10-27T14:02:00+00:00' },
      ],
    });
    expect(csv.split('\r\n')).toEqual([
      'room,meeting start,display name,prolific_id,first spoke at,prolific_id_invalid',
      'study-1,2026-10-27T14:00:00+00:00,Ana,5f2a91b3c4d5e6f708192a3b,2026-10-27T14:01:00+00:00,',
      'study-1,2026-10-27T14:00:00+00:00,Ben,,2026-10-27T14:02:00+00:00,oops',
      '',
    ]);
  });

  it('quotes a name with a comma or quote, so a spreadsheet keeps the columns', () => {
    const csv = peopleCsv({ ...meeting, speakers: [{ display_name: 'Lee, "Al"', prolific_id: null, first_spoke_at: null }] });
    expect(csv.split('\r\n')[1]).toBe('study-1,2026-10-27T14:00:00+00:00,"Lee, ""Al""",,,');
  });

  it('defuses a name a spreadsheet would run as a formula', () => {
    const csv = peopleCsv({ ...meeting, speakers: [{ display_name: '=HYPERLINK("x")', prolific_id: null, first_spoke_at: null }] });
    expect(csv.split('\r\n')[1]).toBe(`study-1,2026-10-27T14:00:00+00:00,"'=HYPERLINK(""x"")",,,`);
  });
});
