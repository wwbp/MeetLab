import Link from 'next/link';

export default function NotFound() {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: 'sans-serif', background: '#0f172a', color: '#cbd5e1' }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '100svh', gap: '1rem' }}>
          <p style={{ fontSize: '0.75rem', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#64748b', margin: 0 }}>404</p>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 500, margin: 0 }}>Page not found</h1>
          <Link href="/" style={{ fontSize: '0.875rem', color: '#94a3b8' }}>← Back to console</Link>
        </div>
      </body>
    </html>
  );
}
