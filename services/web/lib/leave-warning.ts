/**
 * The browser's "Leave site?" prompt while a participant is still in the call.
 *
 * Their completion code only appears after they press Leave; closing the tab
 * instead skips that screen and they cannot be paid. Browsers show their own
 * generic text (custom messages were removed), and only after the page has had
 * a click or keypress — which joining always provides.
 *
 * Returns the function that disarms it.
 */
export function armLeaveWarning(target: EventTarget): () => void {
  const warn = (e: Event) => {
    e.preventDefault();
    (e as BeforeUnloadEvent).returnValue = ''; // older Chrome/Safari need this too
  };
  target.addEventListener('beforeunload', warn);
  return () => target.removeEventListener('beforeunload', warn);
}
