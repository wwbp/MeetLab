import { readFileSync } from 'node:fs';
import path from 'node:path';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { RoomContext, useTrackToggle } from '@livekit/components-react';
import { Room, RoomEvent, Track, type RemoteParticipant } from 'livekit-client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { VideoConferenceComponent } from '@/app/rooms/[roomName]/PageClientImpl';
import { FloorControls } from './FloorButtons';
import { createMicGate, joinMic } from './room-floor';
import type { ConnectionDetails } from './types';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push() {} }) }));
vi.mock('@/lib/fonts', () => ({ inter: { className: '' } }));

const flush = () => vi.advanceTimersByTimeAsync(0);

/** A real Room, whose events and participant are LiveKit's, with the network parts stubbed. */
function fakeRoom() {
  const room = new Room();
  const lp = room.localParticipant;
  const log: string[] = [];
  const track = {
    source: Track.Source.Microphone,
    mute: vi.fn(async () => log.push('mute')),
    stop: vi.fn(),
  };
  const spies = {
    mic: vi.fn(async (on: boolean) => {
      log.push(`mic ${on}`);
      return undefined;
    }),
    camera: vi.fn(async () => undefined),
    data: vi.fn(async (_data: Uint8Array, _options: object) => {}),
    publish: vi.fn(async (_track: unknown, _options: object) => {
      log.push('publish');
      return {};
    }),
    capture: vi.fn(async () => [track]),
  };
  Object.assign(lp, {
    identity: 'me',
    setMicrophoneEnabled: spies.mic,
    setCameraEnabled: spies.camera,
    publishData: spies.data,
    publishTrack: spies.publish,
    createTracks: spies.capture,
  });
  return { room, lp, log, track, ...spies };
}

function addParticipant(room: Room, identity = 'bot_1') {
  const participant = { identity, attributes: {} } as unknown as RemoteParticipant;
  room.remoteParticipants.set(identity, participant);
  return participant;
}

function setFloor(room: Room, bot: RemoteParticipant, holder: string, queue = '') {
  const attributes = { 'floor.holder': holder, 'floor.queue': queue };
  Object.assign(bot, { attributes });
  room.emit(RoomEvent.ParticipantAttributesChanged, attributes, bot);
}

function pageHtml(audioMode: ConnectionDetails['audioMode']) {
  return renderToStaticMarkup(
    createElement(VideoConferenceComponent, {
      connectionDetails: {
        serverUrl: 'ws://x',
        roomName: 'r',
        participantName: 'p',
        participantToken: 't',
        sessionLimitSeconds: 0,
        audioMode,
      },
      userChoices: {
        username: 'p',
        audioEnabled: true,
        videoEnabled: false,
        audioDeviceId: '',
        videoDeviceId: '',
      },
      options: { hq: false, codec: 'vp8' },
    }),
  );
}

const GLOBAL_CSS = readFileSync(path.resolve(__dirname, '../styles/globals.css'), 'utf8');

afterEach(() => vi.useRealTimers());

describe('normal rooms', () => {
  it('test_normal_mic_policy_matches_today: mic follows PreJoin', async () => {
    const on = fakeRoom();
    await joinMic(on.lp, undefined, true);
    expect(on.mic.mock.calls).toEqual([[true]]);

    const off = fakeRoom();
    await joinMic(off.lp, undefined, false);
    expect(off.mic).not.toHaveBeenCalled();
    expect(off.capture).not.toHaveBeenCalled();
  });

  it('test_normal_mic_policy_matches_today: no gate, and the SDK is left alone', () => {
    const { room, lp, mic } = fakeRoom();
    expect(createMicGate(room, 'normal', () => {})).toBeUndefined();
    expect(lp.setMicrophoneEnabled).toBe(mic);
  });

  it('test_normal_mic_policy_matches_today: control-bar toggle shown, no hold button', () => {
    // The bar itself needs a connection to render: the real-browser check sees it. Unmarked, no rule hides it.
    const html = pageHtml('normal');
    expect(html).toContain('lk-room-container');
    expect(html).not.toContain('data-mic-gated');
    expect(html).not.toContain('Hold to talk');
  });

  it('a room with no audio mode, from a server that predates it, is normal', () => {
    const { room } = fakeRoom();
    expect(createMicGate(room, undefined, () => {})).toBeUndefined();
  });
});

describe.each(['ptt', 'baton'] as const)('the other mic paths: %s', (mode) => {
  it('test_ptt_blocks_other_mic_paths: control bar, shortcut and settings hit a dead end', async () => {
    // All three call room.localParticipant.setMicrophoneEnabled, through useTrackToggle.
    const { room, lp, mic, capture } = fakeRoom();
    createMicGate(room, mode, () => {});

    await lp.setMicrophoneEnabled(true);

    expect(mic).not.toHaveBeenCalled();
    expect(capture).not.toHaveBeenCalled();
  });

  it('test_ptt_blocks_other_mic_paths: the toggles are hidden, and only they are', () => {
    expect(pageHtml(mode)).toContain('data-mic-gated');
    const toggle = renderToStaticMarkup(
      createElement(
        RoomContext.Provider,
        { value: new Room() },
        createElement(() =>
          // TrackToggle renders nothing on a server; this is the hook that gives it its props.
          createElement('button', useTrackToggle({ source: Track.Source.Microphone }).buttonProps),
        ),
      ),
    );
    expect(toggle).toMatch(/^<button[^>]*data-lk-source="microphone"/); // what the rule matches
    expect(GLOBAL_CSS).toMatch(
      /\[data-mic-gated\] button\[data-lk-source='microphone'\]\s*{\s*display: none;/,
    );
  });

  it('test_ptt_blocks_other_mic_paths: the camera route stays open', async () => {
    const { room, lp, camera } = fakeRoom();
    createMicGate(room, mode, () => {});

    await lp.setCameraEnabled(true);

    expect(camera).toHaveBeenCalledTimes(1);
  });

  it.each([true, false])(
    'test_ptt_blocks_other_mic_paths: PreJoin mic %s: published muted, opens nothing',
    async (audioEnabled) => {
      vi.useFakeTimers();
      const { room, lp, log, track, publish } = fakeRoom();
      const gate = createMicGate(room, mode, () => {})!;

      joinMic(lp, gate, audioEnabled);
      await flush();

      expect(log).toEqual(['mute', 'publish']); // muted before it can send anything
      expect(publish).toHaveBeenCalledWith(track, { source: Track.Source.Microphone });
    },
  );

  it('the controls are native buttons with a live status', () => {
    const { room } = fakeRoom();
    const gate = createMicGate(room, mode, () => {})!;
    const html = renderToStaticMarkup(createElement(FloorControls, { gate }));

    expect(html).toContain('Hold to talk');
    expect(html).toContain('aria-pressed="false"');
    expect(html).toContain('role="status"');
  });
});

describe('the mic behind the gate', () => {
  it('a press during the muted publish waits for it, so there is only ever one track', async () => {
    vi.useFakeTimers();
    const { room, log } = fakeRoom();
    const gate = createMicGate(room, 'ptt', () => {})!;

    gate.publishMuted();
    gate.floor.press('space');
    await flush();

    expect(log).toEqual(['mute', 'publish', 'mic true']);
  });

  it('a tap during the muted publish never unmutes', async () => {
    vi.useFakeTimers();
    const { room, log } = fakeRoom();
    const gate = createMicGate(room, 'ptt', () => {})!;

    gate.publishMuted();
    gate.floor.press('space');
    gate.floor.release('space');
    await flush();

    expect(log).not.toContain('mic true');
  });

  it('a rejected enable reaches onError, and the status stops saying the mic is on', async () => {
    vi.useFakeTimers();
    const { room, mic } = fakeRoom();
    const denied = new Error('denied');
    mic.mockRejectedValueOnce(denied);
    const onError = vi.fn();
    const gate = createMicGate(room, 'ptt', onError)!;

    gate.floor.press('space');
    expect(gate.status()).toBe('Your microphone is on.');
    await flush();

    expect(onError).toHaveBeenCalledWith(denied);
    expect(gate.status()).toBe('Your microphone is off.');
  });

  it('a failed capture reaches onError, and a later press still asks for the mic', async () => {
    vi.useFakeTimers();
    const { room, mic, capture } = fakeRoom();
    const denied = new Error('denied');
    capture.mockRejectedValueOnce(denied);
    const onError = vi.fn();
    const gate = createMicGate(room, 'ptt', onError)!;

    gate.publishMuted();
    await flush();
    expect(onError).toHaveBeenCalledWith(denied);

    gate.floor.press('space');
    await flush();
    expect(mic).toHaveBeenCalledWith(true);
  });

  it('a publish that fails stops the capture, so nothing keeps the mic device open', async () => {
    vi.useFakeTimers();
    const { room, track, publish } = fakeRoom();
    publish.mockRejectedValueOnce(new Error('no connection'));
    const gate = createMicGate(room, 'ptt', () => {})!;

    gate.publishMuted();
    await flush();

    expect(track.stop).toHaveBeenCalledTimes(1);
  });

  it('push to talk sends no messages and never touches the camera, in 5 cycles', async () => {
    vi.useFakeTimers();
    const { room, mic, data, camera } = fakeRoom();
    const gate = createMicGate(room, 'ptt', () => {})!;

    for (let i = 0; i < 5; i++) {
      gate.floor.press('space');
      await flush();
      gate.floor.release('space');
      await flush();
    }

    expect(mic).toHaveBeenCalledTimes(10);
    expect(data).not.toHaveBeenCalled();
    expect(camera).not.toHaveBeenCalled();
  });

  it('unbinding ends a hold, as bindFloor promises', async () => {
    vi.useFakeTimers();
    const window = new EventTarget();
    const { room, mic } = fakeRoom();
    const unbind = createMicGate(room, 'ptt', () => {})!.bind(window, () => {});
    const down = new Event('keydown');
    Object.defineProperty(down, 'code', { value: 'Space' });
    Object.defineProperty(down, 'target', { value: { tagName: 'BODY' } });

    window.dispatchEvent(down);
    await flush();
    expect(mic.mock.calls).toEqual([[true]]);

    unbind();
    await flush();
    expect(mic.mock.calls).toEqual([[true], [false]]);
  });
});

describe('baton: the contract with the bot', () => {
  function baton(onChange = () => {}) {
    vi.useFakeTimers();
    const r = fakeRoom();
    const bot = addParticipant(r.room);
    const gate = createMicGate(r.room, 'baton', () => {})!;
    const unbind = gate.bind(new EventTarget(), onChange);
    return { ...r, bot, gate, unbind };
  }

  it('claims and releases over reliable data on meetlab.floor', () => {
    const { gate, data } = baton();

    gate.floor.press('space');
    gate.floor.release('space');

    const sent = data.mock.calls.map(([bytes, options]) => [
      JSON.parse(new TextDecoder().decode(bytes)),
      options,
    ]);
    const options = { reliable: true, topic: 'meetlab.floor' };
    expect(sent).toEqual([
      [{ type: 'claim' }, options],
      [{ type: 'release' }, options],
    ]);
  });

  it('you are the holder: the mic opens', async () => {
    const { room, bot, gate, mic } = baton();

    gate.floor.press('space');
    await flush();
    expect(mic).not.toHaveBeenCalled();

    setFloor(room, bot, 'me');
    await flush();
    expect(mic.mock.calls).toEqual([[true]]);
    expect(gate.status()).toBe('You have the floor. Your microphone is on.');
  });

  it('you are in the queue: closed, no fail-open, and the grant opens it', async () => {
    const { room, bot, gate, mic } = baton();

    gate.floor.press('space');
    setFloor(room, bot, 'alice', 'me');
    await vi.advanceTimersByTimeAsync(5000);

    expect(mic).not.toHaveBeenCalled();
    expect(gate.status()).toBe('You are waiting for the floor. Your microphone is off.');

    setFloor(room, bot, 'me');
    await flush();
    expect(mic.mock.calls).toEqual([[true]]);
  });

  it('in the queue with nobody named as holder is still a denial', async () => {
    const { room, bot, gate, mic } = baton();

    gate.floor.press('space');
    setFloor(room, bot, '', 'alice,me');
    await vi.advanceTimersByTimeAsync(5000);

    expect(mic).not.toHaveBeenCalled();
  });

  it('someone else holds: a mic opened by the timeout closes', async () => {
    const { room, bot, gate, mic } = baton();

    gate.floor.press('space');
    await vi.advanceTimersByTimeAsync(1500);
    expect(mic.mock.calls).toEqual([[true]]);

    setFloor(room, bot, 'alice');
    await flush();
    expect(mic.mock.calls).toEqual([[true], [false]]);
    expect(gate.status()).toBe('Someone else has the floor. Your microphone is off.');
  });

  it('names who holds when the room knows them, and the assistant as the assistant', () => {
    const { room, bot, gate } = baton();
    room.remoteParticipants.set('alice', { identity: 'alice', name: 'Alice' } as RemoteParticipant);

    setFloor(room, bot, 'alice');
    expect(gate.status()).toBe('Alice has the floor. Your microphone is off.');

    setFloor(room, bot, 'bot_');
    expect(gate.status()).toBe('The assistant has the floor. Your microphone is off.');
  });

  it('a press with no answer yet says waiting; a free floor says free', () => {
    const { gate } = baton();

    expect(gate.status()).toBe('The floor is free. Your microphone is off.');
    gate.floor.press('space');
    expect(gate.status()).toBe('You are waiting for the floor. Your microphone is off.');
    gate.floor.release('space');
    expect(gate.status()).toBe('The floor is free. Your microphone is off.');
  });

  it('a mic opened by the timeout says so, despite a dead bot naming a holder', async () => {
    const { room, bot, gate } = baton();
    setFloor(room, bot, 'alice');

    gate.floor.press('space');
    await vi.advanceTimersByTimeAsync(1500);

    expect(gate.status()).toBe('You have the floor. Your microphone is on.');
  });

  it('a dead bot stays visible: no answer in 1.5 s opens only while pressed', async () => {
    const { gate, mic } = baton();

    await vi.advanceTimersByTimeAsync(5000);
    expect(mic).not.toHaveBeenCalled();

    gate.floor.press('space');
    await vi.advanceTimersByTimeAsync(1499);
    expect(mic).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(mic.mock.calls).toEqual([[true]]);

    gate.floor.release('space');
    await flush();
    expect(mic.mock.calls).toEqual([[true], [false]]);
  });

  it('the bot leaving opens a waiting press, and nothing unpressed', async () => {
    const { room, bot, gate, mic } = baton();

    room.remoteParticipants.delete('bot_1');
    room.emit(RoomEvent.ParticipantDisconnected, bot);
    await flush();
    expect(mic).not.toHaveBeenCalled();
    room.remoteParticipants.set('bot_1', bot);

    gate.floor.press('space');
    setFloor(room, bot, 'alice', 'me');
    room.remoteParticipants.delete('bot_1');
    room.emit(RoomEvent.ParticipantDisconnected, bot);
    await flush();
    expect(mic.mock.calls).toEqual([[true]]);

    gate.floor.release('space');
    await flush();
    expect(mic.mock.calls).toEqual([[true], [false]]);
  });

  it('with no bot a press opens at once, with no claim', async () => {
    vi.useFakeTimers();
    const { room, mic, data } = fakeRoom();
    const gate = createMicGate(room, 'baton', () => {})!;

    gate.floor.press('pointer');
    await flush();

    expect(mic.mock.calls).toEqual([[true]]);
    expect(data).not.toHaveBeenCalled();
    expect(gate.status()).toBe('Your microphone is on.');
  });

  it('people changing their own attributes are not answers', async () => {
    const { room, gate, mic } = baton();
    const alice = addParticipant(room, 'alice');

    gate.floor.press('space');
    setFloor(room, alice, 'me');
    await flush();

    expect(mic).not.toHaveBeenCalled();
  });

  it('the newest bot speaks for the room while a dead one is still visible', () => {
    const { room, bot, gate } = baton();
    setFloor(room, bot, 'alice');
    addParticipant(room, 'bot_2');

    expect(gate.status()).toBe('The floor is free. Your microphone is off.');
  });

  it('a failed send still fails open, and is not an unhandled rejection', async () => {
    const { lp, gate, mic } = baton();
    // A plain function: vi.fn attaches its own handlers, which would hide an unhandled rejection.
    lp.publishData = () => Promise.reject(new Error('not connected'));

    const unhandled = vi.fn();
    process.once('unhandledRejection', unhandled);

    gate.floor.press('space');
    await vi.advanceTimersByTimeAsync(1500);
    expect(mic.mock.calls).toEqual([[true]]);

    vi.useRealTimers();
    await new Promise((done) => setTimeout(done));
    process.off('unhandledRejection', unhandled);
    expect(unhandled).not.toHaveBeenCalled();
  });

  it('a bot already in the room shows once connected: joining fires no ParticipantConnected', () => {
    const onChange = vi.fn();
    const { room } = baton(onChange);

    room.emit(RoomEvent.Connected);

    expect(onChange).toHaveBeenCalled();
  });

  it('every change tells the page, and unbinding stops it', () => {
    const onChange = vi.fn();
    const { room, bot, gate, unbind } = baton(onChange);

    gate.floor.press('space');
    expect(onChange).toHaveBeenCalled();

    onChange.mockClear();
    setFloor(room, bot, 'alice');
    expect(onChange).toHaveBeenCalled();

    unbind();
    onChange.mockClear();
    setFloor(room, bot, 'me');
    expect(onChange).not.toHaveBeenCalled();
    for (const event of [
      RoomEvent.ParticipantAttributesChanged,
      RoomEvent.Connected,
      RoomEvent.ParticipantConnected,
      RoomEvent.ParticipantDisconnected,
    ]) {
      expect(room.listenerCount(event)).toBe(0);
    }
  });
});
