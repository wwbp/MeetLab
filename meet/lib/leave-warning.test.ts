import { describe, expect, it } from 'vitest';
import { armLeaveWarning } from './leave-warning';

// Node has no BeforeUnloadEvent, and its plain Event.returnValue is read-only.
class FakeBeforeUnloadEvent extends Event {
  declare returnValue: unknown;
  constructor() {
    super('beforeunload', { cancelable: true });
    Object.defineProperty(this, 'returnValue', { value: true, writable: true });
  }
}

function closeTab(target: EventTarget) {
  const e = new FakeBeforeUnloadEvent();
  target.dispatchEvent(e);
  return e;
}

describe('armLeaveWarning', () => {
  it('asks the browser to confirm closing the tab while armed', () => {
    const target = new EventTarget();
    armLeaveWarning(target);
    expect(closeTab(target).defaultPrevented).toBe(true);
  });

  it('lets the tab close once disarmed (participant left via the button)', () => {
    const target = new EventTarget();
    const disarm = armLeaveWarning(target);
    disarm();
    expect(closeTab(target).defaultPrevented).toBe(false);
  });
});
