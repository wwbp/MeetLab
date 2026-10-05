import { inter } from '@/lib/fonts';
import { cn } from '@/lib/utils';
import '@/styles/theme.css';
import { NavLinks } from '@/components/console/nav-links';
import { LogoutButton } from '@/components/console/logout-button';


export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <title>MeetLab</title>
      <div className={cn(inter.variable, 'bg-background text-foreground font-sans antialiased')}>
        <div className="flex h-svh flex-col">
          <header className="border-foreground flex h-14 shrink-0 items-center gap-8 border-b px-6">
            <span className="text-base font-bold tracking-tight">MeetLab</span>
            <div className="flex flex-1 items-center justify-between">
              <NavLinks />
              <LogoutButton />
            </div>
          </header>
          <main className="flex-1 overflow-y-auto">{children}</main>
        </div>
      </div>
    </>
  );
}
