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
      <div className="flex min-h-screen items-center justify-center">
        <div className="w-96 rounded-lg border border-border bg-card p-8 text-center">
          <h1 className="text-xl font-semibold">Invalid meeting link</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            This start link is invalid or was created with a different server key. Ask the person
            who shared it for a new one.
          </p>
        </div>
      </div>
    );
  }

  return <StartClient token={decodeURIComponent(token)} />;
}
