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
  /** What the participant copies into the study survey when they leave. */
  completionCode: string;
};
