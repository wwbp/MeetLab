import { publicLink } from '@/lib/matchmaking-client';
import { resolveLinkParam } from '@/lib/start-link';
import { StartClient } from './StartClient';

export const dynamic = 'force-dynamic';

const Message = ({ heading, message }: { heading: string; message: string }) => (
  <div className="flex min-h-screen items-center px-6 sm:px-16">
    <div className="w-full max-w-md space-y-3">
      <h1 className="text-5xl font-bold tracking-tight">{heading}</h1>
      <p className="text-muted-foreground text-base">{message}</p>
    </div>
  </div>
);

/**
 * Public meeting-start page. GET only resolves the link and asks the runner how it is set
 * up — provisioning happens exclusively on the button's POST, so link unfurlers and
 * prefetchers (which GET) never create tickets, rooms or bots. A stopped link renders as
 * closed in StartClient, after it checks this tab for a ticket to resume (R25).
 */
export default async function StartLinkPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const link = await resolveLinkParam(token);
  const invalid = (
    <Message
      heading="This link doesn’t work"
      message="It may be mistyped or out of date. Ask the person who sent it for a new one."
    />
  );
  if (!link) return invalid;

  const info = await publicLink(link.linkId);
  let settings = { active: true, needs_password: false, size: 1 }; // an old JWT link, unseen (R3)
  if (info.status === 200 && 'size' in info.body) settings = info.body;
  else if (!link.legacyPool) {
    if (info.status === 404) return invalid;
    return (
      <Message
        heading="This page could not load"
        message="Please wait a minute, then reload this page."
      />
    );
  }

  return (
    <StartClient
      token={decodeURIComponent(token)}
      linkId={link.linkId}
      size={settings.size}
      needsPassword={settings.needs_password}
      active={settings.active}
    />
  );
}
