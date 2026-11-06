// One meeting's people as a CSV: who to pay, matched on Prolific ID (docs/study-support.md).
// The shape is the runner's GET /conversations/{id}/speakers.
export type Person = {
  display_name: string | null;
  prolific_id: string | null;
  prolific_id_invalid?: string | null;
  first_spoke_at: string | null;
};
export type MeetingPeople = { room_name: string; started_at: string; speakers: Person[] };

function cell(value: string | null | undefined): string {
  let v = value ?? '';
  // A name typed by a participant must not run as a spreadsheet formula.
  if (/^[=+\-@\t\r]/.test(v)) v = `'${v}`;
  return /[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

export function peopleCsv({ room_name, started_at, speakers }: MeetingPeople): string {
  const rows = [
    ['room', 'meeting start', 'display name', 'prolific_id', 'first spoke at', 'prolific_id_invalid'],
    ...speakers.map((p) => [room_name, started_at, p.display_name, p.prolific_id, p.first_spoke_at, p.prolific_id_invalid]),
  ];
  return rows.map((r) => r.map(cell).join(',')).join('\r\n') + '\r\n';
}
