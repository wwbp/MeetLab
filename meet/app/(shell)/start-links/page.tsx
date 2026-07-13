'use client';

import { useEffect, useState } from 'react';

/**
 * Start Links — pick a pool of bot configs (by scope name), generate a shareable
 * meeting start link. Every click on the link provisions a fresh room with one
 * config uniformly randomly picked from the pool.
 */
export default function StartLinksPage() {
  const [available, setAvailable] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [availPicked, setAvailPicked] = useState<string[]>([]);
  const [selPicked, setSelPicked] = useState<string[]>([]);
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

  function moveRight() {
    setSelected([...selected, ...availPicked]);
    setAvailable(available.filter((s) => !availPicked.includes(s)));
    setAvailPicked([]);
    setLink('');
  }

  function moveLeft() {
    setAvailable([...available, ...selPicked].sort());
    setSelected(selected.filter((s) => !selPicked.includes(s)));
    setSelPicked([]);
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
    setNotice('Link copied to clipboard');
  }

  const box =
    'border-input bg-background h-64 w-full rounded border px-1 py-1 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-ring';

  function pickHandler(setter: (v: string[]) => void) {
    return (e: React.ChangeEvent<HTMLSelectElement>) =>
      setter(Array.from(e.target.selectedOptions).map((o) => o.value));
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6 sm:px-8 sm:py-10">
      <header className="space-y-1">
        <p className="text-muted-foreground font-mono text-xs uppercase">Console</p>
        <h1 className="text-3xl font-medium">Start Links</h1>
        <p className="text-muted-foreground text-sm">
          Pick the bot configs for the pool, then generate a shareable link. Every click on the
          link starts a fresh meeting with one config randomly assigned from the pool.
        </p>
      </header>

      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : (
        <>
          <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-4">
            <div className="space-y-1">
              <p className="text-muted-foreground font-mono text-xs uppercase">Available configs</p>
              <select multiple value={availPicked} onChange={pickHandler(setAvailPicked)} className={box}>
                {available.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-2">
              <button
                onClick={moveRight}
                disabled={availPicked.length === 0}
                className="rounded bg-secondary px-3 py-2 text-sm text-secondary-foreground disabled:opacity-50"
                title="Add to pool"
              >
                →
              </button>
              <button
                onClick={moveLeft}
                disabled={selPicked.length === 0}
                className="rounded bg-secondary px-3 py-2 text-sm text-secondary-foreground disabled:opacity-50"
                title="Remove from pool"
              >
                ←
              </button>
            </div>

            <div className="space-y-1">
              <p className="text-muted-foreground font-mono text-xs uppercase">Selected pool</p>
              <select multiple value={selPicked} onChange={pickHandler(setSelPicked)} className={box}>
                {selected.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="space-y-3">
            <button
              onClick={handleGenerate}
              disabled={selected.length === 0 || generating}
              className="rounded bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
            >
              {generating ? 'Generating…' : `Generate start link (${selected.length} in pool)`}
            </button>

            {link && (
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={link}
                  onFocus={(e) => e.target.select()}
                  className="border-input bg-background w-full rounded border px-3 py-2 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-ring"
                />
                <button
                  onClick={handleCopy}
                  className="rounded bg-secondary px-3 py-2 text-sm text-secondary-foreground"
                >
                  Copy
                </button>
              </div>
            )}

            {notice && <p className="text-sm text-emerald-500">{notice}</p>}
            {error && <p className="text-destructive text-sm">{error}</p>}
          </div>
        </>
      )}
    </div>
  );
}
