export type BrowserSupport = {
  webRTC: boolean;
  mediaDevices: boolean;
  webSocket: boolean;
  sharedArrayBuffer: boolean;
  worker: boolean;
};

export function getBrowserSupport(): BrowserSupport {
  if (typeof window === 'undefined') {
    // SSR — assume supported; gate runs client-side only
    return { webRTC: true, mediaDevices: true, webSocket: true, sharedArrayBuffer: true, worker: true };
  }
  return {
    webRTC: typeof RTCPeerConnection !== 'undefined',
    mediaDevices: typeof navigator !== 'undefined' && typeof navigator.mediaDevices?.getUserMedia === 'function',
    webSocket: typeof WebSocket !== 'undefined',
    sharedArrayBuffer: typeof SharedArrayBuffer !== 'undefined',
    worker: typeof Worker !== 'undefined',
  };
}

/** Returns true when the browser can run core LiveKit functionality (WebRTC + mic/camera + transport). */
export function isCoreSupported(support: BrowserSupport): boolean {
  return support.webRTC && support.mediaDevices && support.webSocket;
}

/**
 * Returns true when the browser supports SharedArrayBuffer and Web Workers,
 * required for E2EE and Krisp noise filtering.
 */
export function isEnhancedSupported(support: BrowserSupport): boolean {
  return support.sharedArrayBuffer && support.worker;
}
