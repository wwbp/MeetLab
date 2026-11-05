/**
 * Who may open the mic in a push-to-talk or baton room. Normal rooms have no floor.
 * The bot enforces the baton too (`meetlab_bot/floor.py`); this is the page's half.
 */

/** `room.localParticipant` fits. Only the mic: the floor has no way to touch the camera. */
export type FloorMic = { setMicrophoneEnabled(on: boolean): unknown };

/** The bot's side of a baton room. Without one, the room is push-to-talk. */
export type Baton = { hasBot(): boolean; claim(): void; release(): void };

type Hold = 'pointer' | 'space' | 'latch';
type Schedule = (fn: () => void, ms: number) => () => void;

// A proposed value (docs/audio-modes-test-plan.md): long enough for a slow bot, short enough to talk.
const CLAIM_TIMEOUT_MS = 1500;

const realSchedule: Schedule = (fn, ms) => {
  const timer = setTimeout(fn, ms);
  return () => clearTimeout(timer);
};

export function createFloor({
  mic,
  baton,
  schedule = realSchedule,
}: {
  mic: FloorMic;
  baton?: Baton;
  schedule?: Schedule;
}) {
  const holds = new Set<Hold>();
  let open = !baton; // may a hold speak? Always in push-to-talk; on an answer in a baton room
  let micOn = false;
  let cancelWait = () => {};

  function sync() {
    const want = open && holds.size > 0;
    if (want !== micOn) {
      micOn = want;
      mic.setMicrophoneEnabled(want);
    }
  }

  /** The bot's answer to a claim, at any time. Also call `answer(true)` when the bot leaves. */
  function answer(granted: boolean) {
    cancelWait();
    open = granted;
    sync();
  }

  function press(hold: Hold) {
    const firstHold = holds.size === 0;
    holds.add(hold);
    if (firstHold && baton) {
      open = !baton.hasBot();
      if (!open) {
        // Armed before the claim, so a claim that fails still fails open.
        cancelWait = schedule(() => answer(true), CLAIM_TIMEOUT_MS);
        baton.claim();
      }
    }
    sync();
  }

  function release(hold: Hold) {
    if (!holds.delete(hold)) return;
    const lastHold = holds.size === 0;
    if (lastHold) cancelWait();
    sync();
    if (lastHold) baton?.release();
  }

  return {
    press,
    release,
    answer,
    /** For `aria-pressed`: a lost floor unlatches, so this can change on its own. */
    isLatched: () => holds.has('latch'),
    /** The latched alternative to holding. Returns its new state. */
    toggle() {
      const latched = !holds.has('latch');
      if (latched) press('latch');
      else release('latch');
      return latched;
    },
  };
}

export type Floor = ReturnType<typeof createFloor>;

const KEEPS_SPACE = new Set(['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A']);

function spaceKeysMic(event: KeyboardEvent) {
  const target = event.target as HTMLElement;
  if (event.repeat || event.ctrlKey || event.metaKey || event.altKey) return false;
  if (target.tagName === 'BUTTON' && 'floorHold' in target.dataset) return true;
  return !target.isContentEditable && !KEEPS_SPACE.has(target.tagName);
}

/**
 * Space, and the releases that can arrive anywhere. Pass `window`. Returns the unbind, which ends
 * any hold: a keyup or pointerup that nobody hears would otherwise leave the mic open.
 */
export function bindFloor(window: EventTarget, floor: Floor) {
  const listeners = new AbortController();
  // Capture: LiveKit's chat input stops its key events bubbling, and a keyup lost there is a hot mic.
  const on = <E extends Event>(type: string, fn: (event: E) => void) =>
    window.addEventListener(type, fn as EventListener, { capture: true, signal: listeners.signal });
  const releaseHolds = () => {
    floor.release('pointer');
    floor.release('space');
  };

  on<KeyboardEvent>('keydown', (event) => {
    if (event.code !== 'Space' || !spaceKeysMic(event)) return;
    event.preventDefault(); // Space would scroll the page
    floor.press('space');
  });
  on<KeyboardEvent>('keyup', (event) => {
    if (event.code === 'Space') floor.release('space');
  });
  on('pointerup', () => floor.release('pointer'));
  on('pointercancel', () => floor.release('pointer'));
  // Not capture: window would then also hear every element losing focus.
  window.addEventListener('blur', releaseHolds, { signal: listeners.signal });

  return () => {
    listeners.abort();
    releaseHolds();
  };
}
