import { inter } from '@/lib/fonts';
import { cn } from '@/lib/utils';
import '@/styles/theme.css';


export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <title>Sign in | MeetLab</title>
      <div className={cn(inter.variable, 'bg-background text-foreground font-sans antialiased')}>
        {children}
      </div>
    </>
  );
}
