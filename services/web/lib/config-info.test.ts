// The Bot Config page's info panel (lib/config-info.ts): every setting a researcher can set
// is explained, with what our tests measured where we have it.
import { describe, expect, it } from 'vitest';
import { CONFIG_INFO, SECTIONS } from './config-info';
import { EMPTY_CONFIG } from './bot-config';

describe('config info', () => {
  it('explains every Bot Config setting', () => {
    for (const key of Object.keys(EMPTY_CONFIG)) {
      expect(CONFIG_INFO[key]?.about, key).toBeTruthy();
    }
  });

  it('puts every setting in exactly one category', () => {
    const placed = SECTIONS.flatMap((s) => s.fields);
    expect(new Set(placed).size).toBe(placed.length);
    expect(new Set(placed)).toEqual(new Set(Object.keys(EMPTY_CONFIG)));
  });

  it('says what each measured result came from', () => {
    for (const [key, info] of Object.entries(CONFIG_INFO)) {
      for (const r of info.results ?? []) expect(r.source, `${key}: ${r.text}`).toBeTruthy();
    }
  });
});
