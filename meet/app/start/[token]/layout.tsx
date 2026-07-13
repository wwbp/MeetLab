import { Public_Sans } from 'next/font/google';
import { cn } from '@/lib/utils';
import '@/styles/theme.css';

const publicSans = Public_Sans({
  variable: '--font-public-sans',
  subsets: ['latin'],
});

export default function StartLinkLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <title>Join meeting | MeetLab</title>
      <div className={cn(publicSans.variable, 'bg-background text-foreground font-sans antialiased')}>
        {children}
      </div>
    </>
  );
}
