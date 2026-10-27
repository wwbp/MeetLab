// The bot's state in plain words for the Rooms page (the raw status stays under Details).
const BOT_STATE: Record<string, string> = {
  missing: 'No bot',
  starting: 'Joining…',
  connected_no_tracks: 'In the room, no audio yet',
  connected: 'In the room',
};

// Past this, a joining bot is most likely waiting for a machine to start.
const SLOW_JOIN_MS = 3 * 60 * 1000;

export function botState(status: string, startedAt: string | undefined, now: number): { label: string; hint?: string } {
  const label = BOT_STATE[status] ?? status;
  const started = startedAt ? Date.parse(startedAt) : NaN;
  if (status !== 'starting' || Number.isNaN(started)) return { label };
  const secs = Math.max(0, Math.floor((now - started) / 1000));
  const m = Math.floor(secs / 60);
  const elapsed = m > 0 ? `${m}m ${secs % 60}s` : `${secs}s`;
  return now - started > SLOW_JOIN_MS
    ? { label: `${label} ${elapsed}`, hint: 'Slow? Use Prepare for study before sessions' }
    : { label: `${label} ${elapsed}` };
}
