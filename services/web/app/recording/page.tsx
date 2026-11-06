import { RecordingClient } from './RecordingClient';

/**
 * What LiveKit's recorder films (the runner's RECORDING_TEMPLATE_URL): it opens this page with
 * its own ?url= and ?token=. Public because the recorder has no console session; without a
 * valid token the page shows and joins nothing.
 */
export default async function RecordingPage({
  searchParams,
}: {
  searchParams: Promise<{ url?: string; token?: string }>;
}) {
  const { url, token } = await searchParams;
  if (!url || !token) return null;
  return <RecordingClient serverUrl={url} token={token} />;
}
