import React from 'react';
import type { Floor } from './floor';
import type { MicGate } from './room-floor';

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

/** What a push-to-talk or baton room shows in place of the mic toggle. */
export function FloorControls({ gate }: { gate: MicGate }) {
  const [, rerender] = React.useReducer((n: number) => n + 1, 0);
  React.useEffect(() => gate.bind(window, rerender), [gate]);
  return (
    <div
      role="group"
      aria-label="Microphone"
      style={{
        position: 'absolute',
        bottom: 80,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 10,
        width: 'max-content',
        maxWidth: 'calc(100% - 2rem)',
        display: 'grid',
        justifyItems: 'center',
        gap: '0.5rem',
        padding: '0.5rem 1rem',
        background: 'var(--lk-bg)',
      }}
    >
      <p role="status" style={{ margin: 0 }}>
        {gate.status()}
      </p>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <HoldButton floor={gate.floor} />
        <LatchButton
          pressed={gate.floor.isLatched()}
          onToggle={() => {
            gate.floor.toggle();
            rerender();
          }}
        />
      </div>
    </div>
  );
}
