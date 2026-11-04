'use client';

import {
  LiveKitRoom,
  RoomAudioRenderer,
  VideoTrack,
  useParticipantTracks,
  useRemoteParticipants,
} from '@livekit/components-react';
import { Track, type Participant } from 'livekit-client';
import { useEffect, useRef, type ReactNode } from 'react';
import { cardLabel, gridColumns, initials } from '@/lib/recording';

/** One person on the recording: always a visible card with their name, video or not.
 *  Inline styles: this page must look the same with nothing else loaded (the recorder films it). */
export function PersonCard({ name, video }: { name: string; video?: ReactNode }) {
  return (
    <div
      data-card="true"
      style={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: 0,
        minHeight: 0,
        overflow: 'hidden',
        borderRadius: 12,
        background: '#1e293b',
        border: '3px solid #64748b',
      }}
    >
      {video ?? (
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 144,
            height: 144,
            borderRadius: '50%',
            background: '#475569',
            color: '#fff',
            fontSize: 56,
          }}
        >
          {initials(name)}
        </span>
      )}
      <span
        style={{
          position: 'absolute',
          left: 12,
          bottom: 12,
          padding: '4px 12px',
          borderRadius: 6,
          background: 'rgba(0, 0, 0, 0.7)',
          color: '#fff',
          fontSize: 26,
        }}
      >
        {name}
      </span>
    </div>
  );
}

function Person({ participant }: { participant: Participant }) {
  const tracks = useParticipantTracks(
    [Track.Source.ScreenShare, Track.Source.Camera],
    participant.identity,
  );
  const shown =
    tracks.find((t) => t.source === Track.Source.ScreenShare) ??
    tracks.find((t) => t.publication?.isSubscribed && !t.publication.isMuted);
  return (
    <PersonCard
      name={cardLabel(participant)}
      video={
        shown ? (
          <VideoTrack
            trackRef={shown}
            style={{ width: '100%', height: '100%', objectFit: 'contain' }}
          />
        ) : undefined
      }
    />
  );
}

function Grid() {
  const people = useRemoteParticipants(); // the recorder itself is hidden and local
  const seen = useRef(false);
  useEffect(() => {
    if (people.length > 0) seen.current = true;
    else if (seen.current) console.log('END_RECORDING'); // everyone has left
  }, [people.length]);
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${gridColumns(people.length)}, minmax(0, 1fr))`,
        gridAutoRows: 'minmax(0, 1fr)',
        gap: 16,
        boxSizing: 'border-box',
        width: '100vw',
        height: '100vh',
        padding: 16,
        background: '#0f172a',
        fontFamily: 'system-ui, sans-serif',
      }}
    >
      {people.map((p) => (
        <Person key={p.identity} participant={p} />
      ))}
    </div>
  );
}

/** The recorder films from START_RECORDING to END_RECORDING (LiveKit's custom template contract). */
export function RecordingClient({ serverUrl, token }: { serverUrl: string; token: string }) {
  return (
    <LiveKitRoom
      serverUrl={serverUrl}
      token={token}
      connect
      audio={false}
      video={false}
      onConnected={() => console.log('START_RECORDING')}
      onDisconnected={() => console.log('END_RECORDING')}
    >
      <Grid />
      <RoomAudioRenderer />
    </LiveKitRoom>
  );
}
