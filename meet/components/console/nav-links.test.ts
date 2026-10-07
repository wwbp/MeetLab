import { existsSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { NAV_ITEMS as NAV_LINKS } from './nav-links';

// The console's Database link pointed at /api/db, which nothing served on v2 (2026-10-06):
// every link the console shows must reach a page or a route in this app.
describe('console navigation', () => {
  it('every link has a page or a route behind it', () => {
    const root = path.resolve(__dirname, '../..');
    for (const link of NAV_LINKS) {
      const seg = link.href.replace(/^\//, '').replace(/\/$/, '');
      const candidates = seg === ''
        ? ['app/(shell)/page.tsx']
        : [`app/(shell)/${seg}/page.tsx`, `app/${seg}/route.ts`, `app/${seg}/[...path]/route.ts`];
      expect(candidates.some((c) => existsSync(path.join(root, c))), `${link.label} → ${link.href}`).toBe(true);
    }
  });
});
