'use client';

import { useState } from 'react';

export function StartClient({ token }: { token: string }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleJoin() {
    setError('');
    setLoading(true);
    try {
      const res = await fetch('/api/start-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
      const data = (await res.json().catch(() => ({}))) as { url?: string; error?: string };
      if (res.ok && data.url) {
        window.location.assign(data.url);
        return; // keep the spinner while the browser navigates
      }
      setError(data.error || 'Failed to start the meeting — please try again');
    } catch {
      setError('Network error — please try again');
    }
    setLoading(false);
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="w-96 rounded-lg border border-border bg-card p-8 text-center">
        <h1 className="text-xl font-semibold">You&apos;re invited to a meeting</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Joining creates a fresh meeting room with an assistant already in it.
        </p>
        <button
          onClick={handleJoin}
          disabled={loading}
          className="mt-6 w-full rounded bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {loading ? 'Setting up your meeting…' : 'Join meeting'}
        </button>
        {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
      </div>
    </div>
  );
}
