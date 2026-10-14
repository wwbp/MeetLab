'use client';

import { useState } from 'react';
import { buttonClass } from '@/components/console/swiss';

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
    <div className="flex min-h-screen items-center px-6 sm:px-16">
      <div className="w-full max-w-md space-y-8">
        <div className="space-y-3">
          <h1 className="text-5xl font-bold tracking-tight">Join the meeting</h1>
          <p className="text-muted-foreground text-base">A new meeting room opens for you, with an assistant already in it.</p>
        </div>
        <button onClick={handleJoin} disabled={loading} className={buttonClass}>
          {loading ? 'Setting up your meeting…' : 'Join meeting'}
        </button>
        {error && <p className="text-signal text-sm">{error}</p>}
      </div>
    </div>
  );
}
