const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);

function extractHostname(hostOrUrl: string): string | null {
  const firstValue = hostOrUrl.split(',')[0]?.trim();
  if (!firstValue) {
    return null;
  }

  try {
    const parsed = new URL(firstValue);
    if (parsed.hostname) {
      return parsed.hostname.toLowerCase();
    }
  } catch {
    // Host headers are often host:port, not full URLs.
  }

  try {
    const parsed = new URL(`http://${firstValue}`);
    return parsed.hostname.toLowerCase();
  } catch {
    return null;
  }
}

function isLoopbackHost(hostname: string | null): boolean {
  if (!hostname) {
    return false;
  }
  return LOOPBACK_HOSTS.has(hostname);
}

export function validateLiveKitPublicUrlForRequestHost(
  livekitPublicUrl: string,
  requestHost: string | null,
): string | null {
  if (!requestHost) {
    return null;
  }

  const requestHostname = extractHostname(requestHost);
  if (isLoopbackHost(requestHostname)) {
    return null;
  }

  let livekitHostname: string | null = null;
  try {
    livekitHostname = new URL(livekitPublicUrl).hostname.toLowerCase();
  } catch {
    return `LIVEKIT_URL_PUBLIC is invalid: ${livekitPublicUrl}`;
  }

  if (isLoopbackHost(livekitHostname)) {
    return [
      `LIVEKIT_URL_PUBLIC resolves to ${livekitHostname}, but this request came from ${requestHostname ?? requestHost}.`,
      'For ngrok/remote access, set LIVEKIT_URL_PUBLIC to a public LiveKit URL (for example your LiveKit Cloud project URL).',
    ].join(' ');
  }

  return null;
}
