import { Public_Sans } from 'next/font/google';
import { ApplyThemeScript, ThemeToggle } from '@/components/agent/theme-toggle';
import { cn } from '@/lib/utils';
import '@/styles/agent-globals.css';

const publicSans = Public_Sans({
  variable: '--font-public-sans',
  subsets: ['latin'],
});

interface LayoutProps {
  children: React.ReactNode;
}

export default function DeskLayout({ children }: LayoutProps) {
  return (
    <>
      <title>Desk | Rooms and Bot Ops</title>
      <ApplyThemeScript />
      <div
        className={cn(
          publicSans.variable,
          'bg-background text-foreground scroll-smooth font-sans antialiased'
        )}
      >
        {children}
        <div className="group fixed bottom-0 left-1/2 z-50 mb-2 -translate-x-1/2">
          <ThemeToggle className="translate-y-20 transition-transform delay-150 duration-300 group-hover:translate-y-0" />
        </div>
      </div>
    </>
  );
}
