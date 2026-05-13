import { Public_Sans } from 'next/font/google';
import { cn } from '@/lib/utils';
import '@/styles/theme.css';
import { NavLinks } from '@/components/console/nav-links';
import { LogoutButton } from '@/components/console/logout-button';

const publicSans = Public_Sans({
  variable: '--font-public-sans',
  subsets: ['latin'],
});

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <title>MeetLab</title>
      <div className={cn(publicSans.variable, 'bg-background text-foreground font-sans antialiased')}>
        <div className="flex min-h-svh flex-col">
          <header className="border-foreground/10 flex h-12 shrink-0 items-center gap-4 border-b px-4">
            <span className="font-mono text-sm font-medium">MeetLab</span>
            <div className="flex flex-1 items-center justify-between">
              <NavLinks />
              <LogoutButton />
            </div>
          </header>
          <main className="flex-1">{children}</main>
        </div>
      </div>
    </>
  );
}
