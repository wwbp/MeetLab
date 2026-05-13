import { Public_Sans } from 'next/font/google';
import { ApplyThemeScript } from '@/components/agent/theme-toggle';
import { cn } from '@/lib/utils';
import '@/styles/agent-globals.css';

const publicSans = Public_Sans({
  variable: '--font-public-sans',
  subsets: ['latin'],
});

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <title>Sign in | MeetLab</title>
      <ApplyThemeScript />
      <div className={cn(publicSans.variable, 'bg-background text-foreground font-sans antialiased')}>
        {children}
      </div>
    </>
  );
}
