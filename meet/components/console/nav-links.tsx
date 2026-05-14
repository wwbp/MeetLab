'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
  { label: 'Rooms & Bots', href: '/' },
  { label: 'Bot Config', href: '/config' },
<<<<<<< HEAD
  { label: 'DB Admin', href: '/db', external: true },
=======
  { label: 'DB Admin', href: '/db' },
>>>>>>> main
];

export function NavLinks() {
  const pathname = usePathname();

  return (
    <nav className="flex items-center gap-1">
<<<<<<< HEAD
      {NAV_ITEMS.map(({ label, href, external }) => {
        const isActive = href === '/' ? pathname === '/' : pathname.startsWith(href);
        const className = cn(
          'rounded px-3 py-1.5 text-sm transition-colors',
          isActive ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'
        );
        return external ? (
          <a key={href} href={href} target="_blank" rel="noopener noreferrer" className={className}>
            {label}
          </a>
        ) : (
          <Link key={href} href={href} className={className}>
=======
      {NAV_ITEMS.map(({ label, href }) => {
        const isActive = href === '/' ? pathname === '/' : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              'rounded px-3 py-1.5 text-sm transition-colors',
              isActive
                ? 'bg-foreground text-background'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
>>>>>>> main
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
