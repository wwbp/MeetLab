import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { LocalParticipant } from 'livekit-client';
import { describe, expect, expectTypeOf, it } from 'vitest';
import { HoldButton, LatchButton } from './FloorButtons';
import { bindFloor, createFloor, type Baton, type FloorMic } from './floor';

/** A stand-in participant that also counts camera calls, which the floor must never make. */
function fakeMic() {
  const calls: boolean[] = [];
  return {
    calls,
    camera: 0,
    setMicrophoneEnabled(on: boolean) {
      calls.push(on);
    },
    setCameraEnabled() {
      this.camera += 1;
    },
  };
}

function fakeClock() {
  let now = 0;
  let timers: { at: number; fn: () => void }[] = [];
  return {
    schedule(fn: () => void, ms: number) {
      const timer = { at: now + ms, fn };
      timers.push(timer);
      return () => {
        timers = timers.filter((t) => t !== timer);
      };
    },
    advance(ms: number) {
      now += ms;
      const due = timers.filter((t) => t.at <= now);
      timers = timers.filter((t) => t.at > now);
      due.forEach((t) => t.fn());
    },
    pending: () => timers.length,
  };
}

function fakeBaton(hasBot = true) {
  const baton: Baton & { claims: number; releases: number } = {
    claims: 0,
    releases: 0,
    hasBot: () => hasBot,
    claim() {
      baton.claims += 1;
    },
    release() {
      baton.releases += 1;
    },
  };
  return baton;
}

/** An event as the browser delivers it; `target` is what had focus, not the window it reached. */
function fire(window: EventTarget, type: string, fields: Record<string, unknown> = {}) {
  const event = new Event(type, { cancelable: true });
  for (const [key, value] of Object.entries(fields)) {
    Object.defineProperty(event, key, { value });
  }
  window.dispatchEvent(event);
  return event;
}

const SPACE = { code: 'Space' };
const CHAT_INPUT = { tagName: 'INPUT' };

function ptt() {
  const mic = fakeMic();
  const window = new EventTarget();
  const floor = createFloor({ mic });
  return { mic, window, floor, unbind: bindFloor(window, floor) };
}

function batonRoom(hasBot = true) {
  const mic = fakeMic();
  const clock = fakeClock();
  const baton = fakeBaton(hasBot);
  const floor = createFloor({ mic, baton, schedule: clock.schedule });
  return { mic, clock, baton, floor };
}

describe('push to talk', () => {
  it('test_ptt_mic_opens_on_hold_closes_on_release: pointer', () => {
    const { mic, window, floor } = ptt();
    const button = HoldButton({ floor });

    button.props.onPointerDown({ button: 0 });
    expect(mic.calls).toEqual([true]);

    fire(window, 'pointerup');
    expect(mic.calls).toEqual([true, false]);
  });

  it('test_ptt_mic_opens_on_hold_closes_on_release: Space, 1 enable despite key repeat', () => {
    const { mic, window } = ptt();

    const down = fire(window, 'keydown', SPACE);
    expect(down.defaultPrevented).toBe(true); // Space would otherwise scroll the page
    fire(window, 'keydown', { ...SPACE, repeat: true });
    fire(window, 'keydown', { ...SPACE, repeat: true });
    fire(window, 'keydown', SPACE); // some platforms repeat without the flag
    expect(mic.calls).toEqual([true]);

    fire(window, 'keyup', SPACE);
    expect(mic.calls).toEqual([true, false]);
  });

  it('a second press takes the same mic; only the last release closes it', () => {
    const { mic, window, floor } = ptt();

    HoldButton({ floor }).props.onPointerDown({ button: 0 });
    fire(window, 'keydown', SPACE);
    fire(window, 'keyup', SPACE);
    expect(mic.calls).toEqual([true]);

    fire(window, 'pointerup');
    expect(mic.calls).toEqual([true, false]);
  });

  it('a right click does not key the mic and cannot open a context menu over the button', () => {
    const { mic, floor } = ptt();
    const { props } = HoldButton({ floor });

    props.onPointerDown({ button: 2 });
    expect(mic.calls).toEqual([]);

    let prevented = false;
    props.onContextMenu({ preventDefault: () => (prevented = true) });
    expect(prevented).toBe(true);
  });

  it.each(['blur', 'pointercancel'])('test_blur_and_pointercancel_release: %s', (type) => {
    const { mic, window, floor } = ptt();

    HoldButton({ floor }).props.onPointerDown({ button: 0 });
    fire(window, type);

    expect(mic.calls).toEqual([true, false]); // no timer ran: same tick
  });

  it('test_blur_and_pointercancel_release: blur ends a Space hold, whose keyup never comes', () => {
    const { mic, window } = ptt();

    fire(window, 'keydown', SPACE);
    fire(window, 'blur');
    expect(mic.calls).toEqual([true, false]);

    fire(window, 'keydown', { ...SPACE, repeat: true }); // still down on return: not a new press
    expect(mic.calls).toEqual([true, false]);
  });

  it('pointercancel does not end a Space hold', () => {
    const { mic, window } = ptt();

    fire(window, 'keydown', SPACE);
    fire(window, 'pointercancel');

    expect(mic.calls).toEqual([true]);
  });

  it.each([
    ['chat input', CHAT_INPUT],
    ['textarea', { tagName: 'TEXTAREA' }],
    ['select', { tagName: 'SELECT' }],
    ['contenteditable', { tagName: 'DIV', isContentEditable: true }],
    ['a button Space would click, like Leave', { tagName: 'BUTTON', dataset: {} }],
    ['a link', { tagName: 'A' }],
  ])('test_space_in_chat_input_never_keys_mic: %s', (_name, target) => {
    const { mic, window } = ptt();

    const down = fire(window, 'keydown', { ...SPACE, target });
    fire(window, 'keyup', { ...SPACE, target });

    expect(mic.calls).toEqual([]);
    expect(down.defaultPrevented).toBe(false); // the space still reaches the input
  });

  it('Space keys the mic with the hold button itself focused', () => {
    const { mic, window } = ptt();

    fire(window, 'keydown', {
      ...SPACE,
      target: { tagName: 'BUTTON', dataset: { floorHold: '' } },
    });

    expect(mic.calls).toEqual([true]);
  });

  it('focus moving into the chat input mid-hold still releases on keyup', () => {
    const { mic, window } = ptt();

    fire(window, 'keydown', SPACE);
    fire(window, 'keyup', { ...SPACE, target: CHAT_INPUT });

    expect(mic.calls).toEqual([true, false]);
  });

  it('hears releases in the capture phase, and only the window itself losing focus', () => {
    // No DOM here to propagate through, so this reads the phases directly. LiveKit's chat input
    // stops its key events bubbling (Chat.tsx), so a bubbling keyup listener would miss the release
    // above. A capturing blur listener would hear every element losing focus and drop the hold.
    const capture: Record<string, unknown> = {};
    class Spy extends EventTarget {
      addEventListener(type: string, fn: EventListener, options: AddEventListenerOptions) {
        capture[type] = options.capture ?? false;
        super.addEventListener(type, fn, options);
      }
    }
    bindFloor(new Spy(), createFloor({ mic: fakeMic() }));

    expect(capture).toEqual({
      keydown: true,
      keyup: true,
      pointerup: true,
      pointercancel: true,
      blur: false,
    });
  });

  it.each(['ctrlKey', 'metaKey', 'altKey'])('%s with Space belongs to the OS', (modifier) => {
    const { mic, window } = ptt();

    fire(window, 'keydown', { ...SPACE, [modifier]: true });

    expect(mic.calls).toEqual([]);
  });

  it('other keys do nothing', () => {
    const { mic, window } = ptt();

    fire(window, 'keydown', { code: 'KeyA' });

    expect(mic.calls).toEqual([]);
  });

  it('unbinding ends the hold and stops listening, so leaving cannot strand a hot mic', () => {
    const { mic, window, unbind } = ptt();

    fire(window, 'keydown', SPACE);
    unbind();
    expect(mic.calls).toEqual([true, false]);

    fire(window, 'keydown', SPACE);
    expect(mic.calls).toEqual([true, false]);
  });
});

describe('latched toggle: the alternative for anyone who cannot hold', () => {
  it('test_toggle_alternative_has_aria_pressed', () => {
    const html = (pressed: boolean) =>
      renderToStaticMarkup(createElement(LatchButton, { pressed, onToggle: () => {} }));

    expect(html(false)).toContain('aria-pressed="false"');
    expect(html(true)).toContain('aria-pressed="true"');
    expect(html(true)).toMatch(/^<button /); // a native button: focus, Enter and Space for free
  });

  it('test_toggle_alternative_has_aria_pressed: its click reaches onToggle', () => {
    let toggled = 0;
    LatchButton({ pressed: false, onToggle: () => (toggled += 1) }).props.onClick();
    expect(toggled).toBe(1);
  });

  it('test_toggle_alternative_has_aria_pressed: latches with no hold, reports its state', () => {
    const { mic, floor } = ptt();

    expect(floor.toggle()).toBe(true);
    expect(mic.calls).toEqual([true]);

    expect(floor.toggle()).toBe(false);
    expect(mic.calls).toEqual([true, false]);
  });

  it('a latch outlives blur and a hold ending, since neither was how it started', () => {
    const { mic, window, floor } = ptt();

    floor.toggle();
    fire(window, 'keydown', SPACE);
    fire(window, 'keyup', SPACE);
    fire(window, 'blur');
    fire(window, 'pointercancel');

    expect(mic.calls).toEqual([true]);
  });

  it('a latch in a baton room waits for the grant like a press', () => {
    const { mic, baton, floor } = batonRoom();

    floor.toggle();
    expect(baton.claims).toBe(1);
    expect(mic.calls).toEqual([]);

    floor.answer(true);
    expect(mic.calls).toEqual([true]);
  });
});

describe('baton', () => {
  it('test_baton_press_waits_for_grant', () => {
    const { mic, baton, floor } = batonRoom();

    floor.press('space');
    expect(baton.claims).toBe(1);
    expect(mic.calls).toEqual([]);

    floor.answer(true);
    expect(mic.calls).toEqual([true]);

    floor.release('space');
    expect(mic.calls).toEqual([true, false]);
    expect(baton.releases).toBe(1);
  });

  it('one claim per hold, whatever else presses meanwhile', () => {
    const { baton, floor } = batonRoom();

    floor.press('space');
    floor.press('space');
    floor.press('pointer');

    expect(baton.claims).toBe(1);
  });

  it('a denial keeps the mic closed, and a later grant to the same hold opens it', () => {
    const { mic, clock, floor } = batonRoom();

    floor.press('space');
    floor.answer(false);
    clock.advance(10_000); // a denial is an answer: this is not a silent bot
    expect(mic.calls).toEqual([]);

    floor.answer(true);
    expect(mic.calls).toEqual([true]);
  });

  it('a grant arriving after the release opens nothing', () => {
    const { mic, floor } = batonRoom();

    floor.press('space');
    floor.release('space');
    floor.answer(true);

    expect(mic.calls).toEqual([]);
  });

  it('the next hold claims again and waits again', () => {
    const { mic, baton, floor } = batonRoom();

    floor.press('space');
    floor.answer(true);
    floor.release('space');
    floor.press('space');

    expect(baton.claims).toBe(2);
    expect(mic.calls).toEqual([true, false]);
  });

  it('test_no_bot_opens_floor: no bot, the mic opens at once and with no claim', () => {
    const { mic, baton, floor } = batonRoom(false);

    floor.press('pointer');

    expect(mic.calls).toEqual([true]);
    expect(baton.claims).toBe(0);
  });

  it('test_no_bot_opens_floor: no bot still means press to talk, and release closes', () => {
    const { mic, floor } = batonRoom(false);

    expect(mic.calls).toEqual([]);
    floor.press('pointer');
    floor.release('pointer');

    expect(mic.calls).toEqual([true, false]);
  });

  it('test_no_bot_opens_floor: no answer in 1.5 s opens the mic, not before', () => {
    const { mic, clock, floor } = batonRoom();

    floor.press('space');
    clock.advance(1499);
    expect(mic.calls).toEqual([]);

    clock.advance(1);
    expect(mic.calls).toEqual([true]);
  });

  it('test_no_bot_opens_floor: the claim throwing still fails open', () => {
    const { mic, clock, baton, floor } = batonRoom();
    baton.claim = () => {
      throw new Error('not connected');
    };

    expect(() => floor.press('space')).toThrow();
    clock.advance(1500);

    expect(mic.calls).toEqual([true]);
  });

  it('a release before the 1.5 s cancels the wait, so nothing opens later', () => {
    const { mic, clock, floor } = batonRoom();

    floor.press('space');
    floor.release('space');

    expect(clock.pending()).toBe(0);
    clock.advance(1500);
    expect(mic.calls).toEqual([]);
  });

  it('a grant cancels the wait', () => {
    const { clock, floor } = batonRoom();

    floor.press('space');
    floor.answer(true);

    expect(clock.pending()).toBe(0);
  });

  it('a denial after failing open closes the mic: the answer wins', () => {
    const { mic, clock, floor } = batonRoom();

    floor.press('space');
    clock.advance(1500);
    floor.answer(false);

    expect(mic.calls).toEqual([true, false]);
  });

  it('a hold after failing open has to ask again', () => {
    const { mic, clock, baton, floor } = batonRoom();

    floor.press('space');
    clock.advance(1500);
    floor.release('space');
    floor.press('space');

    expect(baton.claims).toBe(2);
    expect(mic.calls).toEqual([true, false]);
  });

  it('blur during the wait closes everything and cancels the fail-open', () => {
    const { mic, clock, floor } = batonRoom();
    const window = new EventTarget();
    bindFloor(window, floor);

    fire(window, 'keydown', SPACE);
    fire(window, 'blur');
    clock.advance(1500);

    expect(mic.calls).toEqual([]);
  });
});

describe('the camera', () => {
  it('test_camera_untouched_by_floor_changes: push to talk, 5 cycles', () => {
    const { mic, floor } = ptt();

    for (let i = 0; i < 5; i++) {
      floor.press('space');
      floor.release('space');
    }

    expect(mic.calls).toHaveLength(10);
    expect(mic.camera).toBe(0);
  });

  it('test_camera_untouched_by_floor_changes: baton, 5 cycles', () => {
    const { mic, floor } = batonRoom();

    for (let i = 0; i < 5; i++) {
      floor.press('space');
      floor.answer(true);
      floor.release('space');
    }

    expect(mic.calls).toHaveLength(10);
    expect(mic.camera).toBe(0);
  });
});

it('the real participant fits the mic interface, so tests here hold for the wiring', () => {
  expectTypeOf<LocalParticipant>().toExtend<FloorMic>();
});
