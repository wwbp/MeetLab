'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
  { label: 'Rooms & Bots', href: '/' },
  { label: 'Meetings', href: '/meetings' },
  { label: 'Errors & Events', href: '/events' },
  { label: 'Bot Config', href: '/config' },
  { label: 'Start Links', href: '/start-links' },
  { label: 'DB Admin', href: '/api/db', external: true },
];

export function NavLinks() {
  const pathname = usePathname();

  return (
    <nav className="flex items-center gap-6">
      {NAV_ITEMS.map(({ label, href, external }) => {
        const isActive = href === '/' ? pathname === '/' : pathname.startsWith(href);
        const className = cn(
          // The current page: black, with the signal-red rule under it.
          'border-b-2 py-4 text-sm transition-colors',
          isActive ? 'border-signal text-foreground font-medium' : 'text-muted-foreground hover:text-foreground border-transparent'
        );
        return external ? (
          <a key={href} href={href} target="_blank" rel="noopener noreferrer" className={className}>
            {label}
          </a>
        ) : (
          <Link key={href} href={href} className={className}>
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
