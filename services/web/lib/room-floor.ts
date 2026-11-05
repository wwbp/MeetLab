/**
 * The floor (`floor.ts`) on a real Room: closes every other way to the mic, and speaks the
 * contract with the bot (docs/audio-modes-test-plan.md). Normal rooms get no gate.
 */
import {
  RoomEvent,
  Track,
  type LocalParticipant,
  type Participant,
  type Room,
} from 'livekit-client';
import { bindFloor, createFloor, type Baton } from './floor';
import type { ConnectionDetails } from './types';

const FLOOR_TOPIC = 'meetlab.floor';
// The bot only takes the floor after 15 s of silence (floor.py IDLE_SECS). Any sooner, it's the echo
// of this person's own quick release and re-press, with the new grant on its way. Keep below that.
const REAL_LOSS_AFTER_MS = 5000;

const isBot = (identity: string) => identity.startsWith('bot_');

function readBaton(bot: Participant | undefined) {
  return {
    holder: bot?.attributes['floor.holder'] ?? '',
    queue: (bot?.attributes['floor.queue'] ?? '').split(','),
  };
}

/** Undefined for a normal room. Call once per Room: it takes over `setMicrophoneEnabled`. */
export function createMicGate(
  room: Room,
  mode: ConnectionDetails['audioMode'] | undefined,
  onError: (error: Error) => void,
) {
  if (mode !== 'ptt' && mode !== 'baton') return undefined;
  const lp = room.localParticipant;
  let micOn = false;
  let claiming = false;
  let iHold = false; // the bot named me holder of this hold
  let grantedAt = 0;
  let lost = false; // and then stopped: closed until this hold ends. No re-queue, by decision.
  let changed = () => {};

  // The control bar, the settings menu and the shortcut all end here, through useTrackToggle.
  const setMic = lp.setMicrophoneEnabled.bind(lp);
  lp.setMicrophoneEnabled = async () => undefined;

  // One at a time and in order, so a press can't start a second track beside the muted publish.
  let steps: Promise<unknown> = Promise.resolve();
  const enqueue = (step: () => Promise<unknown>) => {
    steps = steps.then(step).catch((error) => {
      micOn = false;
      changed();
      onError(error);
    });
  };

  const newestBot = () =>
    [...room.remoteParticipants.values()].filter((p) => isBot(p.identity)).at(-1);
  const send = (type: 'claim' | 'release') => {
    claiming = type === 'claim';
    iHold = false;
    lost = false;
    changed();
    lp.publishData(new TextEncoder().encode(JSON.stringify({ type })), {
      reliable: true,
      topic: FLOOR_TOPIC,
    }).catch(() => {}); // a lost claim fails open by timeout; a lost release, by the bot's idle limit
  };
  const baton: Baton = {
    hasBot: () => newestBot() !== undefined,
    claim: () => send('claim'),
    release: () => send('release'),
  };

  const floor = createFloor({
    baton: mode === 'baton' ? baton : undefined,
    mic: {
      setMicrophoneEnabled(on) {
        micOn = on;
        if (on) lost = false;
        changed();
        enqueue(() => setMic(micOn)); // the latest wish: a tap during the publish never unmutes
      },
    },
  });

  return {
    floor,

    /** The track exists from join, so a press only unmutes it. Ignores PreJoin's choice. */
    publishMuted() {
      enqueue(async () => {
        const [track] = await lp.createTracks({ audio: true });
        try {
          await track.mute();
          await lp.publishTrack(track, { source: Track.Source.Microphone });
        } catch (error) {
          track.stop();
          throw error;
        }
      });
    },

    /** Space, releases and the bot's answers. Pass `window`; `onChange` re-renders the status. */
    bind(window: EventTarget, onChange: () => void) {
      changed = onChange;
      const onAttributes = (_changed: unknown, participant: Participant) => {
        // Only the newest bot: a replacement's claim is out, so the old one has no say.
        if (participant.identity === newestBot()?.identity) {
          const { holder, queue } = readBaton(participant);
          if (holder === lp.identity) {
            if (!lost) {
              if (!iHold) grantedAt = Date.now();
              iHold = claiming;
              floor.answer(true);
            }
          } else if (iHold) {
            iHold = false;
            const realLoss = Date.now() - grantedAt >= REAL_LOSS_AFTER_MS;
            floor.answer(false);
            if (realLoss) {
              floor.release('latch'); // a latch left pressed would claim a mic that's closed
              claiming = false;
              lost = true; // after that release, which clears it
            }
          } else if (holder || queue.includes(lp.identity)) floor.answer(false);
        }
        changed();
      };
      // This bot has never heard of a hold in progress, and would drop its audio unannounced.
      const claimAgain = () => {
        iHold = false;
        if ((claiming || micOn) && newestBot()) send('claim');
        changed();
      };
      const onJoined = (participant: Participant) => {
        if (isBot(participant.identity)) claimAgain();
        changed();
      };
      const onLeft = (participant: Participant) => {
        // A dead bot leaves ~22 s late, maybe long after its replacement answered.
        if (isBot(participant.identity) && !newestBot()) {
          iHold = false;
          if (!lost) floor.answer(true);
        }
        changed();
      };
      if (mode === 'baton') {
        room.on(RoomEvent.ParticipantAttributesChanged, onAttributes);
        room.on(RoomEvent.Connected, claimAgain); // a bot already here fires no ParticipantConnected
        room.on(RoomEvent.ParticipantConnected, onJoined);
        room.on(RoomEvent.ParticipantDisconnected, onLeft);
      }
      const unbind = bindFloor(window, floor);
      return () => {
        unbind();
        room.off(RoomEvent.ParticipantAttributesChanged, onAttributes);
        room.off(RoomEvent.Connected, claimAgain);
        room.off(RoomEvent.ParticipantConnected, onJoined);
        room.off(RoomEvent.ParticipantDisconnected, onLeft);
        changed = () => {};
      };
    },

    /** One plain sentence: what the mic is doing, and in a baton room, why. */
    status() {
      if (lost) return 'You no longer have the floor. Press again to talk.';
      const bot = newestBot();
      if (mode === 'ptt' || !bot) return `Your microphone is ${micOn ? 'on' : 'off'}.`;
      // Only on the bot's word: a mic opened by the timeout may be one the bot is dropping.
      if (micOn) return `${iHold ? 'You have the floor. ' : ''}Your microphone is on.`;
      const { holder, queue } = readBaton(bot);
      if (queue.includes(lp.identity) || (claiming && !holder)) {
        return 'You are waiting for the floor. Your microphone is off.';
      }
      if (holder && holder !== lp.identity) {
        const who = isBot(holder)
          ? 'The assistant'
          : room.remoteParticipants.get(holder)?.name || 'Someone else';
        return `${who} has the floor. Your microphone is off.`;
      }
      return 'The floor is free. Your microphone is off.';
    },
  };
}

export type MicGate = NonNullable<ReturnType<typeof createMicGate>>;

/** The mic at join. Normal: PreJoin's choice, as ever. With a gate: there, and muted. */
export function joinMic(lp: LocalParticipant, gate: MicGate | undefined, audioEnabled: boolean) {
  if (gate) gate.publishMuted();
  else if (audioEnabled) return lp.setMicrophoneEnabled(true);
  return Promise.resolve();
}
