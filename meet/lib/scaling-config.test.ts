import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';

/**
 * meet is pinned to a single instance, and that is a correctness constraint
 * rather than a capacity choice.
 *
 * Seven modules under lib/ keep domain state on `globalThis` — bot-room claims,
 * the start lock, participant presence, request history. Those are per-process
 * Maps. With two instances behind a load balancer, a request landing on
 * instance B cannot see the claim instance A recorded, so both conclude a room
 * has no bot and start one. The start lock cannot prevent it, because the lock
 * is per-process too. Raising MaxSize therefore adds a race rather than
 * capacity, and the symptom shows up under exactly the load that motivated the
 * change.
 *
 * This is a test rather than a comment because unpinning would look entirely
 * reasonable: the runner scales out, load is high, meet is the obvious next
 * lever. Every behavioural test would still pass while rooms quietly got two
 * bots. Here it fails, and names the modules responsible.
 *
 * It also fails once the state is gone — prompting the pin to be lifted rather
 * than left in place forever.
 */

const LIB = join(process.cwd(), 'lib');
const EBEXTENSIONS = join(process.cwd(), '.ebextensions');

function modulesWithGlobalState(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      modulesWithGlobalState(path, found);
    } else if (entry.name.endsWith('.ts') && !entry.name.includes('.test.')) {
      if (readFileSync(path, 'utf8').includes('globalThis')) {
        found.push(path.slice(LIB.length + 1));
      }
    }
  }
  return found.sort();
}

function optionSettings(): Record<string, string> {
  const found: Record<string, string> = {};
  if (!existsSync(EBEXTENSIONS)) return found;
  for (const name of readdirSync(EBEXTENSIONS).filter((f) => f.endsWith('.config'))) {
    const text = readFileSync(join(EBEXTENSIONS, name), 'utf8');
    for (const key of ['MinSize', 'MaxSize', 'InstanceType']) {
      const m = text.match(new RegExp(`^\\s*${key}:\\s*(.+?)\\s*$`, 'm'));
      if (m) found[key] = m[1].trim();
    }
  }
  return found;
}

describe('meet horizontal scaling', () => {
  it('still keeps domain state in memory', () => {
    // If this fails the state has moved, and the pin below can be reconsidered.
    expect(modulesWithGlobalState(LIB).length).toBeGreaterThan(0);
  });

  it('is not scaled out while that is true', () => {
    const opts = optionSettings();
    if (!('MaxSize' in opts)) return; // no autoscaling config at all: cannot scale out
    expect(
      Number(opts.MaxSize),
      `meet keeps bot-room claims and start locks on globalThis, so two instances ` +
        `disagree about which rooms already have a bot. Offending modules: ` +
        `${modulesWithGlobalState(LIB).join(', ')}`,
    ).toBe(1);
  });

  it('has vertical headroom instead', () => {
    const opts = optionSettings();
    if (!('InstanceType' in opts)) return;
    expect(
      opts.InstanceType,
      't3 instances are burstable and throttle after sustained load',
    ).not.toContain('t3.');
  });
});
