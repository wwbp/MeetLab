'use client';

import { useEffect, useState } from 'react';
import { Label, PageHeader, buttonClass, inputClass, secondaryButtonClass } from '@/components/console/swiss';

/**
 * Start links: pick saved bot settings for the pool (by name), and create a shareable
 * meeting start link. Every click on the link provisions a fresh room with one
 * config uniformly randomly picked from the pool.
 */
export default function StartLinksPage() {
  const [available, setAvailable] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [link, setLink] = useState('');
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
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
  }, []);

  const [query, setQuery] = useState('');
  // Not yet chosen, filtered by the search.
  const shown = available.filter((x) => !selected.includes(x) && x.toLowerCase().includes(query.trim().toLowerCase()));

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
        body: JSON.stringify({ pool: selected }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError((data as { error?: string }).error ?? 'Failed to generate link');
        return;
      }
      setLink((data as { url: string }).url);
    } catch {
      setError('Network error generating link');
    } finally {
      setGenerating(false);
    }
  }

  async function handleCopy() {
    await navigator.clipboard.writeText(link);
    setNotice('Copied.');
  }

  return (
    <div className="mx-auto w-full max-w-6xl px-6 py-10">
      <PageHeader
        title="Start links"
        lead="A link for participants. Each time it’s opened it starts a new meeting with a bot, using one of the settings you add, picked at random."
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

          {/* The chosen ones, and the button right under them */}
          <div className="space-y-3">
            <Label>In this link {selected.length > 0 && <span className="text-muted-foreground font-normal">({selected.length}, one picked at random per meeting)</span>}</Label>
            <div className="border-foreground h-[23.25rem] overflow-y-auto rounded-sm border">
              {selected.length === 0 && <p className="text-muted-foreground p-3 text-sm">Click settings on the left to add them.</p>}
              {selected.map((scope) => (
                <button key={scope} onClick={() => toggle(scope)} className="hover:bg-muted flex w-full items-center justify-between px-3 py-2 text-left font-mono text-sm">
                  {scope}
                  <span className="text-muted-foreground font-sans" aria-label={`Remove ${scope}`}>×</span>
                </button>
              ))}
            </div>
            <button onClick={handleGenerate} disabled={selected.length === 0 || generating} className={buttonClass}>
              {generating ? 'Creating…' : 'Create link'}
            </button>
          </div>

          {(link || notice || error) && (
            <div className="space-y-2 sm:col-span-2">
              {link && (
                <div className="flex items-center gap-2">
                  <input readOnly value={link} onFocus={(e) => e.target.select()} className={inputClass + ' font-mono text-xs'} />
                  <button onClick={handleCopy} className={secondaryButtonClass}>Copy</button>
                </div>
              )}
              {notice && <p className="text-sm">{notice}</p>}
              {error && <p className="text-signal text-sm">{error}</p>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
