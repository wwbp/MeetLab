'use client';

import { Suspense, useState, type FormEvent } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Label, buttonClass, inputClass } from '@/components/console/swiss';

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch('/api/console/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        router.push(params.get('from') || '/');
      } else {
        const data = await res.json().catch(() => ({}));
        setError((data as { error?: string }).error || 'Login failed');
      }
    } catch {
      setError('Network error — please try again');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center px-6 sm:px-16">
      <form onSubmit={handleSubmit} className="w-full max-w-sm space-y-8">
        <div className="space-y-2">
          <h1 className="text-5xl font-bold tracking-tight">MeetLab</h1>
          <p className="text-muted-foreground text-base">Console for the lab&apos;s researchers.</p>
        </div>
        <label className="border-foreground block space-y-1.5 border-t pt-6">
          <Label>Password</Label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
            autoFocus
            required
          />
        </label>
        {error && <p className="text-signal text-sm">{error}</p>}
        <button type="submit" disabled={loading} className={buttonClass}>
          {loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
