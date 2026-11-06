import { LocalAudioTrack, LocalVideoTrack, videoCodecs } from 'livekit-client';
import { VideoCodec } from 'livekit-client';



export function isVideoCodec(codec: string): codec is VideoCodec {
  return videoCodecs.includes(codec as VideoCodec);
}

export type ConnectionDetails = {
  serverUrl: string;
  roomName: string;
  participantName: string;
  participantToken: string;
  /** Advisory session cap for this room, in seconds. 0 = unlimited. */
  sessionLimitSeconds: number;
  /** This room's audio mode. Nothing acts on it yet (docs/audio-modes-test-plan.md). */
  audioMode: 'normal' | 'ptt' | 'baton';
};
