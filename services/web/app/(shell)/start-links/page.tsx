'use client';

import { useCallback, useEffect, useState } from 'react';
import { Label, PageHeader, buttonClass, inputClass, secondaryButtonClass, textActionClass } from '@/components/console/swiss';
import type { LinkRow } from '@/lib/matchmaking-client';

/**
 * Start links: pick saved bot settings for the pool (by name), set the room size and
 * timing, and create a link. The runner keeps each link: the list below shows them all
 * with live counts, and a link can be stopped and started again.
 */
export default function StartLinksPage() {
  const [available, setAvailable] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState('');
  const [size, setSize] = useState(1);
  const [minSize, setMinSize] = useState(1);
  const [delay, setDelay] = useState(0);
  const [maxWaitMin, setMaxWaitMin] = useState(5);
  const [password, setPassword] = useState('');
  const [links, setLinks] = useState<LinkRow[]>([]);
  const [link, setLink] = useState('');
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  const loadLinks = useCallback(async () => {
    try {
      const res = await fetch('/api/console/start-link', { cache: 'no-store' });
      const data = await res.json();
      if (!res.ok) return setError((data as { error?: string }).error ?? 'Failed to load links');
      setLinks((data as { links: LinkRow[] }).links);
    } catch {
      setError('Network error loading links');
    }
  }, []);

  useEffect(() => {
    (async () => {
      void loadLinks();
      try {
        const res = await fetch('/api/console/configs', { cache: 'no-store' });
        const data = await res.json();
        if (!res.ok) {
          setError((data as { error?: string }).error ?? 'Failed to load configs');
          return;
        }
        setAvailable((data.configs as Array<{ scope: string }>).map((c) => c.scope));
      } catch {
        setError('Network error loading configs');
      } finally {
        setLoading(false);
      }
    })();
  }, [loadLinks]);

  const [query, setQuery] = useState('');
  // Not yet chosen, filtered by the search.
  const shown = available.filter((x) => !selected.includes(x) && x.toLowerCase().includes(query.trim().toLowerCase()));
  const group = size > 1;
  const minSizeError = group && !(minSize >= 1 && minSize <= size) ? `At least 1 and at most ${size}.` : '';

  function toggle(scope: string) {
    setSelected(selected.includes(scope) ? selected.filter((x) => x !== scope) : [...selected, scope]);
    setLink('');
  }

  async function handleGenerate() {
    setGenerating(true);
    setNotice('');
    setError('');
    try {
      const res = await fetch('/api/console/start-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          pool: selected,
          size,
          min_size: group ? minSize : size,
          delay_s: delay,
          max_wait_s: maxWaitMin * 60,
          password,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError((data as { error?: string }).error ?? 'Failed to generate link');
        return;
      }
      setLink((data as { url: string }).url);
      setPassword('');
      loadLinks();
    } catch {
      setError('Network error generating link');
    } finally {
      setGenerating(false);
    }
  }

  async function copy(url: string) {
    await navigator.clipboard.writeText(url);
    setNotice('Copied.');
  }

  async function setActive(row: LinkRow, active: boolean) {
    setError('');
    try {
      const res = await fetch(`/api/console/start-link/${encodeURIComponent(row.link_id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active }),
      });
      if (!res.ok) setError(((await res.json()) as { error?: string }).error ?? 'Failed to change the link');
    } catch {
      setError('Network error changing the link');
    }
    loadLinks();
  }

  // A number field: NaN while it is empty (shown empty; the runner never gets it, linkSettings refuses it).
  const number = (set: (n: number) => void) => (e: React.ChangeEvent<HTMLInputElement>) => set(e.target.valueAsNumber);
  const shownNumber = (n: number) => (Number.isNaN(n) ? '' : n);

  return (
    <div className="mx-auto w-full max-w-6xl px-6 py-10">
      <PageHeader
        title="Start links"
        lead="A link for participants. With room size 1, each time it’s opened it starts a new meeting with a bot. With a larger size, participants wait until enough have arrived, then meet together with one bot. Each meeting uses one of the settings you add. A participant gets a completion code only if the bot heard them speak."
      />

      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : (
        <div className="grid gap-8 sm:grid-cols-2">
          {/* All saved bot settings: search, click to add */}
          <div className="space-y-3">
            <Label>Saved bot settings</Label>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" className={inputClass} />
            <div className="border-foreground/30 h-80 overflow-y-auto rounded-sm border">
              {shown.length === 0 && <p className="text-muted-foreground p-3 text-sm">{available.length === 0 ? 'None saved yet: create some in Bot settings.' : 'No match.'}</p>}
              {shown.map((scope) => (
                <button key={scope} onClick={() => toggle(scope)} className="hover:bg-muted flex w-full items-center justify-between px-3 py-2 text-left font-mono text-sm">
                  {scope}
                  <span className="text-muted-foreground font-sans">Add</span>
                </button>
              ))}
            </div>
          </div>

          {/* The chosen ones */}
          <div className="space-y-3">
            <Label>In this link {selected.length > 0 && <span className="text-muted-foreground font-normal">({selected.length}, the least used so far picked per meeting)</span>}</Label>
            <div className="border-foreground h-[23.25rem] overflow-y-auto rounded-sm border">
              {selected.length === 0 && <p className="text-muted-foreground p-3 text-sm">Click settings on the left to add them.</p>}
              {selected.map((scope) => (
                <button key={scope} onClick={() => toggle(scope)} className="hover:bg-muted flex w-full items-center justify-between px-3 py-2 text-left font-mono text-sm">
                  {scope}
                  <span className="text-muted-foreground font-sans" aria-label={`Remove ${scope}`}>×</span>
                </button>
              ))}
            </div>
          </div>

          {/* The link's settings, and the button under them */}
          <div className="grid gap-4 sm:col-span-2 sm:grid-cols-3">
            <label className="space-y-1 sm:col-span-3">
              <Label>Name</Label>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Pilot 3, groups of 3" className={inputClass} />
            </label>
            <label className="space-y-1">
              <Label>Room size (people)</Label>
              <input type="number" min={1} max={8} value={shownNumber(size)} onChange={number((n) => { setSize(n); setMinSize(n); })} className={inputClass} />
            </label>
            {group && (
              <label className="space-y-1">
                <Label>Min size</Label>
                <input type="number" min={1} max={size} value={shownNumber(minSize)} onChange={number(setMinSize)} aria-invalid={!!minSizeError} className={inputClass} />
                <span className={minSizeError ? 'text-signal block text-xs' : 'text-muted-foreground block text-xs'}>
                  {minSizeError || 'If not enough people arrive within max wait, start with at least this many.'}
                </span>
              </label>
            )}
            <label className="space-y-1">
              <Label>Start delay (seconds)</Label>
              <input type="number" min={0} max={300} value={shownNumber(delay)} onChange={number(setDelay)} className={inputClass} />
            </label>
            <label className="space-y-1">
              <Label>Max wait (minutes)</Label>
              <input type="number" min={1} max={30} value={shownNumber(maxWaitMin)} onChange={number(setMaxWaitMin)} className={inputClass} />
            </label>
            <label className="space-y-1 sm:col-span-2">
              <Label>Password</Label>
              <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} />
              <span className="text-muted-foreground block text-xs">Optional. You cannot see it again after you create the link: put it in your survey now.</span>
            </label>
            {group && (
              <p className="text-muted-foreground text-sm sm:col-span-3">
                When a group forms, each person must press Ready within 20 seconds. Anyone who doesn’t is taken out of the line; the others keep their place.
              </p>
            )}
            <div className="sm:col-span-3">
              <button onClick={handleGenerate} disabled={selected.length === 0 || !name.trim() || !!minSizeError || generating} className={buttonClass}>
                {generating ? 'Creating…' : 'Create link'}
              </button>
            </div>
          </div>

          {(link || notice || error) && (
            <div className="space-y-2 sm:col-span-2">
              {link && (
                <div className="flex items-center gap-2">
                  <input readOnly value={link} onFocus={(e) => e.target.select()} className={inputClass + ' font-mono text-xs'} />
                  <button onClick={() => copy(link)} className={secondaryButtonClass}>Copy</button>
                </div>
              )}
              {notice && <p className="text-sm">{notice}</p>}
              {error && <p role="alert" className="text-signal text-sm">{error}</p>}
            </div>
          )}

          <LinkList links={links} onCopy={copy} onSetActive={setActive} />
        </div>
      )}
    </div>
  );
}

function LinkList({ links, onCopy, onSetActive }: { links: LinkRow[]; onCopy: (url: string) => void; onSetActive: (row: LinkRow, active: boolean) => void }) {
  if (links.length === 0) return null;
  return (
    <div className="overflow-x-auto sm:col-span-2">
      <table className="w-full text-left text-sm">
        <thead className="text-muted-foreground border-b">
          <tr>
            {['Name', 'Created', 'Bot settings', 'Size', 'Delay', 'Password', 'Status', 'Waiting now', 'In rooms', 'Expired', 'Missed', ''].map((h) => (
              <th key={h} className="py-2 pr-4 font-normal">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {links.map((row) => {
            const c = row.counts;
            return (
              <tr key={row.link_id} className="border-foreground/15 border-b align-top">
                <td className="py-3 pr-4">{row.legacy ? 'Older link' : row.name}</td>
                <td className="py-3 pr-4 whitespace-nowrap">{new Date(row.created_at).toLocaleString()}</td>
                <td className="py-3 pr-4 font-mono text-xs">{row.pool.join(', ')}</td>
                <td className="py-3 pr-4 tabular-nums">{row.size}</td>
                <td className="py-3 pr-4 tabular-nums">{row.delay_s} s</td>
                <td className="py-3 pr-4">{row.has_password ? 'Yes' : 'No'}</td>
                <td className="py-3 pr-4">{row.active ? 'Active' : 'Stopped'}</td>
                <td className="py-3 pr-4 tabular-nums">{c.searching + c.found + c.ready}</td>
                <td className="py-3 pr-4 tabular-nums">{c.placed + c.done}</td>
                <td className="py-3 pr-4 tabular-nums">{c.expired}</td>
                <td className="py-3 pr-4 tabular-nums">{c.removed}</td>
                <td className="space-x-3 py-3 whitespace-nowrap">
                  {/* An older link's URL is the one already sent; its ID here is only a hash of it. */}
                  {!row.legacy && (
                    <button onClick={() => onCopy(`${window.location.origin}/start/${row.link_id}`)} className={textActionClass}>Copy link</button>
                  )}
                  <button onClick={() => onSetActive(row, !row.active)} className={textActionClass}>{row.active ? 'Stop' : 'Start'}</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
