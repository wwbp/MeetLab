import { Public_Sans } from 'next/font/google';
import { headers } from 'next/headers';
import { ApplyThemeScript, ThemeToggle } from '@/components/agent/theme-toggle';
import { cn, getAppConfig, getStyles } from '@/lib/utils';
import '@/styles/agent-globals.css';

const publicSans = Public_Sans({
  variable: '--font-public-sans',
  subsets: ['latin'],
});

interface LayoutProps {
  children: React.ReactNode;
}

export default async function AgentLayout({ children }: LayoutProps) {
  const hdrs = await headers();
  const appConfig = await getAppConfig(hdrs);
  const { pageTitle, pageDescription } = appConfig;
  const styles = getStyles(appConfig);

  return (
    <>
      <title>{pageTitle}</title>
      <meta name="description" content={pageDescription} />
      {styles && <style>{styles}</style>}
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
