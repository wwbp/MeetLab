'use client';

import { useEffect, useState } from 'react';
import { getBrowserSupport, isCoreSupported } from '@/lib/browser-support';

const SUPPORTED_BROWSERS = [
  { name: 'Google Chrome', version: '96 or later' },
  { name: 'Microsoft Edge', version: '96 or later' },
  { name: 'Mozilla Firefox', version: '119 or later' },
  { name: 'Apple Safari', version: '17 or later (macOS and iOS)' },
];

/**
 * Renders a full-screen blocking gate when the browser lacks core WebRTC APIs.
 * Renders nothing on SSR and on supported browsers.
 */
export function UnsupportedBrowserGate() {
  const [unsupported, setUnsupported] = useState(false);

  useEffect(() => {
    const support = getBrowserSupport();
    if (!isCoreSupported(support)) {
      setUnsupported(true);
    }
  }, []);

  if (!unsupported) return null;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#0a0a0a',
        color: '#f5f5f5',
        fontFamily: 'system-ui, sans-serif',
        padding: '2rem',
      }}
      role="alert"
      aria-live="assertive"
    >
      <div style={{ maxWidth: '480px', textAlign: 'center' }}>
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="48"
          height="48"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#f59e0b"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ margin: '0 auto 1.5rem' }}
          aria-hidden="true"
        >
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>

        <h1 style={{ fontSize: '1.5rem', fontWeight: 700, marginBottom: '0.75rem' }}>
          Browser Not Supported
        </h1>

        <p style={{ color: '#a3a3a3', marginBottom: '1.5rem', lineHeight: 1.6 }}>
          This app requires WebRTC for real-time audio and video. Your current browser does not
          support the required APIs.
        </p>

        <p style={{ fontWeight: 600, marginBottom: '0.75rem' }}>Supported browsers:</p>

        <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 1.5rem', color: '#a3a3a3' }}>
          {SUPPORTED_BROWSERS.map(({ name, version }) => (
            <li key={name} style={{ padding: '0.25rem 0' }}>
              {name} — {version}
            </li>
          ))}
        </ul>

        <p style={{ fontSize: '0.875rem', color: '#737373' }}>
          Please update your browser or switch to a supported one and try again.
        </p>
      </div>
    </div>
  );
}
