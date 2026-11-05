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
        if (isBot(participant.identity)) {
          const { holder, queue } = readBaton(participant);
          if (holder === lp.identity) floor.answer(true);
          else if (holder || queue.includes(lp.identity)) floor.answer(false);
        }
        changed();
      };
      const onLeft = (participant: Participant) => {
        if (isBot(participant.identity)) floor.answer(true);
        changed();
      };
      if (mode === 'baton') {
        room.on(RoomEvent.ParticipantAttributesChanged, onAttributes);
        room.on(RoomEvent.Connected, changed); // a bot already here fires no ParticipantConnected
        room.on(RoomEvent.ParticipantConnected, changed);
        room.on(RoomEvent.ParticipantDisconnected, onLeft);
      }
      const unbind = bindFloor(window, floor);
      return () => {
        unbind();
        room.off(RoomEvent.ParticipantAttributesChanged, onAttributes);
        room.off(RoomEvent.Connected, changed);
        room.off(RoomEvent.ParticipantConnected, changed);
        room.off(RoomEvent.ParticipantDisconnected, onLeft);
        changed = () => {};
      };
    },

    /** One plain sentence: what the mic is doing, and in a baton room, why. */
    status() {
      const bot = newestBot();
      if (mode === 'ptt' || !bot) return `Your microphone is ${micOn ? 'on' : 'off'}.`;
      if (micOn) return 'You have the floor. Your microphone is on.';
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
