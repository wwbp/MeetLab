import type { Floor } from './floor';

/** Pressing is here; the release is `bindFloor`'s, since a pointer can leave the button. */
export function HoldButton({ floor }: { floor: Pick<Floor, 'press'> }) {
  return (
    <button
      type="button"
      className="lk-button"
      data-floor-hold=""
      // A touch that scrolls or long-presses would otherwise end in pointercancel or a menu.
      style={{ touchAction: 'none', userSelect: 'none', WebkitUserSelect: 'none' }}
      onPointerDown={(event: { button: number }) => {
        if (event.button === 0) floor.press('pointer');
      }}
      onContextMenu={(event: { preventDefault(): void }) => event.preventDefault()}
    >
      Hold to talk
    </button>
  );
}

/** Keeps the mic open with no holding. The label stays put: `aria-pressed` is the state. */
export function LatchButton({ pressed, onToggle }: { pressed: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="lk-button" aria-pressed={pressed} onClick={() => onToggle()}>
      Keep mic open
    </button>
  );
}
