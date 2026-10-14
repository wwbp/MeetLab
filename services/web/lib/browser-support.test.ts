import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getBrowserSupport, isCoreSupported, isEnhancedSupported } from './browser-support';

// vitest runs in 'node' env — window is undefined by default, matching SSR behaviour.
// We use vi.stubGlobal to simulate browser environments.

describe('getBrowserSupport', () => {
  describe('SSR (window undefined)', () => {
    it('returns all true when window is undefined', () => {
      const support = getBrowserSupport();
      expect(support.webRTC).toBe(true);
      expect(support.mediaDevices).toBe(true);
      expect(support.webSocket).toBe(true);
      expect(support.sharedArrayBuffer).toBe(true);
      expect(support.worker).toBe(true);
    });
  });

  describe('browser simulation', () => {
    beforeEach(() => {
      vi.stubGlobal('window', {});
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it('detects WebRTC as supported when RTCPeerConnection exists', () => {
      vi.stubGlobal('RTCPeerConnection', class RTCPeerConnection {});
      vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: () => Promise.resolve() } });
      vi.stubGlobal('WebSocket', class WebSocket {});

      const support = getBrowserSupport();
      expect(support.webRTC).toBe(true);
      expect(support.mediaDevices).toBe(true);
      expect(support.webSocket).toBe(true);
    });

    it('detects WebRTC as unsupported when RTCPeerConnection is missing', () => {
      vi.stubGlobal('RTCPeerConnection', undefined);
      vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: () => Promise.resolve() } });
      vi.stubGlobal('WebSocket', class WebSocket {});

      const support = getBrowserSupport();
      expect(support.webRTC).toBe(false);
    });

    it('detects mediaDevices as unsupported when navigator.mediaDevices is missing', () => {
      vi.stubGlobal('RTCPeerConnection', class RTCPeerConnection {});
      vi.stubGlobal('navigator', {});
      vi.stubGlobal('WebSocket', class WebSocket {});

      const support = getBrowserSupport();
      expect(support.mediaDevices).toBe(false);
    });

    it('detects mediaDevices as unsupported when getUserMedia is not a function', () => {
      vi.stubGlobal('RTCPeerConnection', class RTCPeerConnection {});
      vi.stubGlobal('navigator', { mediaDevices: {} });
      vi.stubGlobal('WebSocket', class WebSocket {});

      const support = getBrowserSupport();
      expect(support.mediaDevices).toBe(false);
    });

    it('detects WebSocket as unsupported when missing', () => {
      vi.stubGlobal('RTCPeerConnection', class RTCPeerConnection {});
      vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: () => Promise.resolve() } });
      vi.stubGlobal('WebSocket', undefined);

      const support = getBrowserSupport();
      expect(support.webSocket).toBe(false);
    });

    it('detects SharedArrayBuffer support', () => {
      vi.stubGlobal('RTCPeerConnection', class RTCPeerConnection {});
      vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: () => Promise.resolve() } });
      vi.stubGlobal('WebSocket', class WebSocket {});
      vi.stubGlobal('SharedArrayBuffer', class SharedArrayBuffer {});
      vi.stubGlobal('Worker', class Worker {});

      const support = getBrowserSupport();
      expect(support.sharedArrayBuffer).toBe(true);
      expect(support.worker).toBe(true);
    });

    it('detects missing SharedArrayBuffer', () => {
      vi.stubGlobal('RTCPeerConnection', class RTCPeerConnection {});
      vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: () => Promise.resolve() } });
      vi.stubGlobal('WebSocket', class WebSocket {});
      vi.stubGlobal('SharedArrayBuffer', undefined);
      vi.stubGlobal('Worker', undefined);

      const support = getBrowserSupport();
      expect(support.sharedArrayBuffer).toBe(false);
      expect(support.worker).toBe(false);
    });
  });
});

describe('isCoreSupported', () => {
  it('returns true when all core features are present', () => {
    expect(isCoreSupported({ webRTC: true, mediaDevices: true, webSocket: true, sharedArrayBuffer: false, worker: false })).toBe(true);
  });

  it('returns false when WebRTC is missing', () => {
    expect(isCoreSupported({ webRTC: false, mediaDevices: true, webSocket: true, sharedArrayBuffer: true, worker: true })).toBe(false);
  });

  it('returns false when mediaDevices is missing', () => {
    expect(isCoreSupported({ webRTC: true, mediaDevices: false, webSocket: true, sharedArrayBuffer: true, worker: true })).toBe(false);
  });

  it('returns false when WebSocket is missing', () => {
    expect(isCoreSupported({ webRTC: true, mediaDevices: true, webSocket: false, sharedArrayBuffer: true, worker: true })).toBe(false);
  });
});

describe('isEnhancedSupported', () => {
  it('returns true when SharedArrayBuffer and Worker are present', () => {
    expect(isEnhancedSupported({ webRTC: true, mediaDevices: true, webSocket: true, sharedArrayBuffer: true, worker: true })).toBe(true);
  });

  it('returns false when SharedArrayBuffer is missing', () => {
    expect(isEnhancedSupported({ webRTC: true, mediaDevices: true, webSocket: true, sharedArrayBuffer: false, worker: true })).toBe(false);
  });

  it('returns false when Worker is missing', () => {
    expect(isEnhancedSupported({ webRTC: true, mediaDevices: true, webSocket: true, sharedArrayBuffer: true, worker: false })).toBe(false);
  });
});
