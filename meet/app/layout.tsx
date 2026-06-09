import '../styles/globals.css';
import '@livekit/components-styles';
import '@livekit/components-styles/prefabs';
import type { Metadata, Viewport } from 'next';
import Script from 'next/script';
import { ClientToaster } from '@/components/client-toaster';
import { UnsupportedBrowserGate } from '@/components/UnsupportedBrowserGate';
import { THEME_MEDIA_QUERY, THEME_STORAGE_KEY } from '@/lib/utils';

const THEME_SCRIPT = `
  const doc = document.documentElement;
  const theme = localStorage.getItem("${THEME_STORAGE_KEY}") ?? "system";
  if (theme === "system") {
    doc.classList.add(window.matchMedia("${THEME_MEDIA_QUERY}").matches ? "dark" : "light");
  } else {
    doc.classList.add(theme);
  }
`.trim().replace(/\n/g, '').replace(/\s+/g, ' ');

export const metadata: Metadata = {
  title: {
    default: 'LiveKit Meet | Conference app build with LiveKit open source',
    template: '%s',
  },
  description:
    'LiveKit is an open source WebRTC project that gives you everything needed to build scalable and real-time audio and/or video experiences in your applications.',
  twitter: {
    creator: '@livekitted',
    site: '@livekitted',
    card: 'summary_large_image',
  },
  openGraph: {
    url: 'https://meet.livekit.io',
    images: [
      {
        url: 'https://meet.livekit.io/images/livekit-meet-open-graph.png',
        width: 2000,
        height: 1000,
        type: 'image/png',
      },
    ],
    siteName: 'LiveKit Meet',
  },
  icons: {
    icon: {
      rel: 'icon',
      url: '/favicon.ico',
    },
    apple: [
      {
        rel: 'apple-touch-icon',
        url: '/images/livekit-apple-touch.png',
        sizes: '180x180',
      },
      { rel: 'mask-icon', url: '/images/livekit-safari-pinned-tab.svg', color: '#070707' },
    ],
  },
};

export const viewport: Viewport = {
  themeColor: '#070707',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body data-lk-theme="default">
        <Script id="theme-script" strategy="beforeInteractive" dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
        <UnsupportedBrowserGate />
        <ClientToaster />
        {children}
      </body>
    </html>
  );
}
