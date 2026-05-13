'use client';

import { useRouter } from 'next/navigation';

export function LogoutButton() {
  const router = useRouter();

  async function handleLogout() {
    await fetch('/api/console/logout', { method: 'POST' });
    router.push('/login');
  }

  return (
    <button
      onClick={handleLogout}
      className="text-muted-foreground hover:text-foreground rounded px-3 py-1.5 text-sm transition-colors"
    >
      Logout
    </button>
  );
}
