import { describe, it, expect } from 'vitest';
import { describeCapacity, untilFromLocal } from './prepare-study';

describe('describeCapacity', () => {
  it('explains a pool that is not warm, in plain words', () => {
    expect(describeCapacity({ available: true, min_instances: 0, ready_instances: 0, sessions_per_instance: 3, warm_until: null }))
      .toBe('Not prepared: the first bot of a study waits about 2 minutes for a machine.');
  });

  it('says how many sessions are ready and until when', () => {
    const until = '2026-10-02T15:00:00+00:00';
    const text = describeCapacity({ available: true, min_instances: 2, ready_instances: 1, sessions_per_instance: 3, warm_until: until });
    expect(text).toContain('1 of 2 machines ready');
    expect(text).toContain('about 6 sessions');
    expect(text).toContain(new Date(until).toLocaleString());
  });

  it('passes the runner reason through when there is no pool', () => {
    expect(describeCapacity({ available: false, reason: 'bots run as local containers here' })).toBe('bots run as local containers here');
  });
});

describe('describeCapacity with the always-warm baseline', () => {
  it('says how many sessions the warm machines hold before anything is prepared', () => {
    expect(describeCapacity({ available: true, min_instances: 1, ready_instances: 1, sessions_per_instance: 3, warm_until: null }))
      .toBe('Always ready for about 3 sessions at once. Prepare for more.');
  });
});

describe('describeCapacity with a stuck machine', () => {
  it('asks for an engineer when a machine is stuck', () => {
    const text = describeCapacity({ available: true, min_instances: 0, ready_instances: 0, sessions_per_instance: 3, warm_until: null, unhealthy_instances: 1 });
    expect(text).toContain('1 machine is stuck');
    expect(text).toContain('engineer');
  });
});

describe('untilFromLocal', () => {
  it('turns the form time into an instant the runner accepts (with a zone)', () => {
    expect(untilFromLocal('2026-10-02T15:00')).toBe(new Date('2026-10-02T15:00').toISOString());
  });
});
