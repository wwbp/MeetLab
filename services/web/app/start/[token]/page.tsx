import { verifyStartLink } from '@/lib/start-link';
import { StartClient } from './StartClient';

/**
 * Public meeting-start page. GET only verifies the token and renders a button —
 * provisioning happens exclusively on the button's POST, so link unfurlers and
 * prefetchers (which GET) never create rooms or start bots.
 */
export default async function StartLinkPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const pool = await verifyStartLink(decodeURIComponent(token));

  if (!pool) {
    return (
      <div className="flex min-h-screen items-center px-6 sm:px-16">
        <div className="w-full max-w-md space-y-3">
          <h1 className="text-5xl font-bold tracking-tight">This link doesn’t work</h1>
          <p className="text-muted-foreground text-base">
            It may be mistyped or out of date. Ask the person who sent it for a new one.
          </p>
        </div>
      </div>
    );
  }

  return <StartClient token={decodeURIComponent(token)} />;
}
