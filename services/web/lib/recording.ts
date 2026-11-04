/** What the recording page (app/recording) draws. Pure so it can be tested. */

/** Columns for n cards: the fewest rows that keep cards large on a 16:9 recording. */
export function gridColumns(n: number): number {
  if (n <= 1) return 1;
  if (n <= 4) return 2;
  if (n <= 9) return 3;
  return 4;
}

/** The name on a card: the person's name, else their identity without its suffix. */
export function cardLabel(p: { name?: string; identity: string }): string {
  if (p.identity.startsWith('bot_')) return p.name || 'Assistant';
  return p.name || p.identity.split('__')[0];
}

export function initials(name: string): string {
  const letters = name
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0].toUpperCase());
  return letters.join('') || '?';
}
