'use client';

import React from 'react';
import { decodePassphrase } from '@/lib/client-utils';
import { DebugMode } from '@/lib/Debug';
import { KeyboardShortcuts } from '@/lib/KeyboardShortcuts';
import { RecordingIndicator } from '@/lib/RecordingIndicator';
import { ConnectionDetails } from '@/lib/types';
import {
  formatChatMessageLinks,
  LocalUserChoices,
  PreJoin,
  RoomContext,
  VideoConference,
} from '@livekit/components-react';
import { SettingsMenu } from '@/lib/SettingsMenu';
import {
  ExternalE2EEKeyProvider,
  RoomOptions,
  VideoCodec,
  VideoPresets,
  Room,
  DeviceUnsupportedError,
  RoomConnectOptions,
  RoomEvent,
  TrackPublishDefaults,
  VideoCaptureOptions,
} from 'livekit-client';
import { useRouter } from 'next/navigation';
import { useSetupE2EE } from '@/lib/useSetupE2EE';
import { useLowCPUOptimizer } from '@/lib/usePerfomanceOptimiser';

const CONN_DETAILS_ENDPOINT =
  process.env.NEXT_PUBLIC_CONN_DETAILS_ENDPOINT ?? '/api/connection-details';

type ConferenceErrorBoundaryState = {
  recoverable: boolean;
  fatalError: Error | null;
};

class ConferenceErrorBoundary extends React.Component<
  { onRecover: () => void; children: React.ReactNode },
  ConferenceErrorBoundaryState
> {
  state: ConferenceErrorBoundaryState = { recoverable: false, fatalError: null };

  static getDerivedStateFromError(error: Error): ConferenceErrorBoundaryState {
    if (error.message.includes('Element not part of the array')) {
      return { recoverable: true, fatalError: null };
    }
    return { recoverable: false, fatalError: error };
  }

  componentDidCatch(error: Error) {
    if (error.message.includes('Element not part of the array')) {
      console.warn('Recovering from known LiveKit layout teardown error:', error.message);
      this.props.onRecover();
    }
  }

  render() {
    if (this.state.fatalError) {
      throw this.state.fatalError;
    }
    if (this.state.recoverable) {
      return null;
    }
    return this.props.children;
  }
}

// Returns true when an error message pattern suggests an auth/permission rejection
// rather than a network connectivity issue. Auth errors won't be fixed by relay.
function looksLikeAuthError(error: Error): boolean {
  const msg = error.message.toLowerCase();
  return (
    msg.includes('unauthorized') ||
    msg.includes('not authorized') ||
    msg.includes('forbidden') ||
    msg.includes('invalid token') ||
    msg.includes('token expired') ||
    msg.includes('permission') ||
    msg.includes('401') ||
    msg.includes('403')
  );
}

export function PageClientImpl(props: {
  roomName: string;
  region?: string;
  hq: boolean;
  codec: VideoCodec;
}) {
  const [preJoinChoices, setPreJoinChoices] = React.useState<LocalUserChoices | undefined>(
    undefined,
  );
  const preJoinDefaults = React.useMemo(() => {
    return {
      username: '',
      videoEnabled: true,
      audioEnabled: true,
    };
  }, []);
  const [connectionDetails, setConnectionDetails] = React.useState<ConnectionDetails | undefined>(
    undefined,
  );

  // When a direct (STUN) connection fails we remount VideoConferenceComponent
  // with forceRelay=true so LiveKit uses TURN-only ICE candidates.
  const [forceRelay, setForceRelay] = React.useState(false);
  const [conferenceKey, setConferenceKey] = React.useState(0);

  const handleConnectivityFailure = React.useCallback(() => {
    console.warn('[relay] Direct connection failed — retrying via TURN relay');
    setForceRelay(true);
    setConferenceKey((k) => k + 1);
  }, []);

  const handlePreJoinSubmit = React.useCallback(async (values: LocalUserChoices) => {
    setPreJoinChoices(values);
    const url = new URL(CONN_DETAILS_ENDPOINT, window.location.origin);
    url.searchParams.append('roomName', props.roomName);
    url.searchParams.append('participantName', values.username);
    if (props.region) {
      url.searchParams.append('region', props.region);
    }
    const connectionDetailsResp = await fetch(url.toString());
    const connectionDetailsData = await connectionDetailsResp.json();
    setConnectionDetails(connectionDetailsData);
  }, []);
  const handlePreJoinError = React.useCallback((e: any) => console.error(e), []);

  return (
    <main data-lk-theme="default" style={{ height: '100%' }}>
      {connectionDetails === undefined || preJoinChoices === undefined ? (
        <div style={{ display: 'grid', placeItems: 'center', height: '100%' }}>
          <PreJoin
            defaults={preJoinDefaults}
            onSubmit={handlePreJoinSubmit}
            onError={handlePreJoinError}
          />
        </div>
      ) : (
        <VideoConferenceComponent
          key={conferenceKey}
          connectionDetails={connectionDetails}
          userChoices={preJoinChoices}
          options={{ codec: props.codec, hq: props.hq }}
          forceRelay={forceRelay}
          onConnectivityFailure={!forceRelay ? handleConnectivityFailure : undefined}
        />
      )}
    </main>
  );
}

function VideoConferenceComponent(props: {
  userChoices: LocalUserChoices;
  connectionDetails: ConnectionDetails;
  options: {
    hq: boolean;
    codec: VideoCodec;
  };
  forceRelay?: boolean;
  onConnectivityFailure?: () => void;
}) {
  const keyProvider = new ExternalE2EEKeyProvider();
  const { worker, e2eePassphrase } = useSetupE2EE();
  const e2eeEnabled = !!(e2eePassphrase && worker);

  const [e2eeSetupComplete, setE2eeSetupComplete] = React.useState(false);

  const roomOptions = React.useMemo((): RoomOptions => {
    let videoCodec: VideoCodec | undefined = props.options.codec ? props.options.codec : 'vp9';
    if (e2eeEnabled && (videoCodec === 'av1' || videoCodec === 'vp9')) {
      videoCodec = undefined;
    }
    const videoCaptureDefaults: VideoCaptureOptions = {
      deviceId: props.userChoices.videoDeviceId ?? undefined,
      resolution: props.options.hq ? VideoPresets.h2160 : VideoPresets.h720,
    };
    const publishDefaults: TrackPublishDefaults = {
      dtx: false,
      videoSimulcastLayers: props.options.hq
        ? [VideoPresets.h1080, VideoPresets.h720]
        : [VideoPresets.h540, VideoPresets.h216],
      red: !e2eeEnabled,
      videoCodec,
    };
    return {
      videoCaptureDefaults: videoCaptureDefaults,
      publishDefaults: publishDefaults,
      audioCaptureDefaults: {
        deviceId: props.userChoices.audioDeviceId ?? undefined,
      },
      adaptiveStream: true,
      dynacast: true,
      e2ee: keyProvider && worker && e2eeEnabled ? { keyProvider, worker } : undefined,
      singlePeerConnection: true,
    };
  }, [props.userChoices, props.options.hq, props.options.codec]);

  const room = React.useMemo(() => new Room(roomOptions), []);

  React.useEffect(() => {
    if (e2eeEnabled) {
      keyProvider
        .setKey(decodePassphrase(e2eePassphrase))
        .then(() => {
          room.setE2EEEnabled(true).catch((e) => {
            if (e instanceof DeviceUnsupportedError) {
              alert(
                `You're trying to join an encrypted meeting, but your browser does not support it. Please update it to the latest version and try again.`,
              );
              console.error(e);
            } else {
              throw e;
            }
          });
        })
        .then(() => setE2eeSetupComplete(true));
    } else {
      setE2eeSetupComplete(true);
    }
  }, [e2eeEnabled, room, e2eePassphrase]);

  const connectOptions = React.useMemo((): RoomConnectOptions => {
    return {
      autoSubscribe: true,
      // rtcConfig lives on RoomConnectOptions (not RoomOptions).
      // Force TURN-only relay when direct UDP has been confirmed to fail;
      // on the first attempt 'all' lets ICE negotiate the fastest path.
      rtcConfig: props.forceRelay ? { iceTransportPolicy: 'relay' } : { iceTransportPolicy: 'all' },
    };
  }, [props.forceRelay]);

  React.useEffect(() => {
    room.on(RoomEvent.Disconnected, handleOnLeave);
    room.on(RoomEvent.EncryptionError, handleEncryptionError);
    room.on(RoomEvent.MediaDevicesError, handleError);

    if (e2eeSetupComplete) {
      room
        .connect(
          props.connectionDetails.serverUrl,
          props.connectionDetails.participantToken,
          connectOptions,
        )
        .catch((error) => {
          // If this looks like a network/ICE failure (not an auth rejection) and
          // the caller supports a relay retry, hand control back up rather than
          // surfacing a raw alert.
          if (!looksLikeAuthError(error) && props.onConnectivityFailure) {
            console.warn('[relay] Connection failed, handing off to relay retry:', error.message);
            props.onConnectivityFailure();
          } else {
            handleError(error);
          }
        });
      if (props.userChoices.videoEnabled) {
        room.localParticipant.setCameraEnabled(true).catch((error) => {
          handleError(error);
        });
      }
      if (props.userChoices.audioEnabled) {
        room.localParticipant.setMicrophoneEnabled(true).catch((error) => {
          handleError(error);
        });
      }
    }
    return () => {
      room.off(RoomEvent.Disconnected, handleOnLeave);
      room.off(RoomEvent.EncryptionError, handleEncryptionError);
      room.off(RoomEvent.MediaDevicesError, handleError);
    };
  }, [e2eeSetupComplete, room, props.connectionDetails, props.userChoices]);

  const lowPowerMode = useLowCPUOptimizer(room);

  const router = useRouter();
  const handleOnLeave = React.useCallback(() => {
    // Defer navigation one tick so LiveKit can finish internal layout teardown.
    window.setTimeout(() => router.push('/'), 0);
  }, [router]);
  const handleError = React.useCallback((error: Error) => {
    console.error(error);
    alert(`Encountered an unexpected error, check the console logs for details: ${error.message}`);
  }, []);
  const handleEncryptionError = React.useCallback((error: Error) => {
    console.error(error);
    alert(
      `Encountered an unexpected encryption error, check the console logs for details: ${error.message}`,
    );
  }, []);

  React.useEffect(() => {
    if (lowPowerMode) {
      console.warn('Low power mode enabled');
    }
  }, [lowPowerMode]);

  return (
    <div className="lk-room-container">
      {props.forceRelay && (
        <div
          style={{
            position: 'absolute',
            top: 8,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 10,
            background: 'rgba(0,0,0,0.55)',
            color: '#fbbf24',
            fontSize: '0.75rem',
            padding: '4px 12px',
            borderRadius: 4,
            pointerEvents: 'none',
            whiteSpace: 'nowrap',
          }}
          title="Direct UDP failed — connection is routed through a TURN relay server"
        >
          Relay connection active
        </div>
      )}
      <RoomContext.Provider value={room}>
        <KeyboardShortcuts />
        <ConferenceErrorBoundary onRecover={handleOnLeave}>
          <VideoConference chatMessageFormatter={formatChatMessageLinks} SettingsComponent={SettingsMenu} />
        </ConferenceErrorBoundary>
        <DebugMode />
        <RecordingIndicator />
      </RoomContext.Provider>
    </div>
  );
}
