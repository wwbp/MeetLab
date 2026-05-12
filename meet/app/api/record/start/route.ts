import { EgressClient, EncodedFileOutput, S3Upload } from 'livekit-server-sdk';
import { NextRequest, NextResponse } from 'next/server';
import { getServerConfig } from '@/lib/config/server';

export async function GET(req: NextRequest) {
  try {
    const roomName = req.nextUrl.searchParams.get('roomName');
    if (!roomName) {
      return new NextResponse('Missing roomName parameter', { status: 400 });
    }

    const config = getServerConfig();
    if (!config.livekitInternalUrl) {
      return new NextResponse('LiveKit server URL is not configured', { status: 500 });
    }

    const hostURL = new URL(config.livekitInternalUrl);
    hostURL.protocol = hostURL.protocol === 'ws:' ? 'http:' : 'https:';

    const egressClient = new EgressClient(
      hostURL.origin,
      config.livekitApiKey,
      config.livekitApiSecret,
    );

    const existing = await egressClient.listEgress({ roomName });
    if (existing.some((e) => e.status < 2)) {
      return new NextResponse('Meeting is already being recorded', { status: 409 });
    }

    const fileOutput = buildFileOutput(roomName);

    await egressClient.startRoomCompositeEgress(
      roomName,
      { file: fileOutput },
      { layout: 'grid' },
    );

    return new NextResponse(null, { status: 200 });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to start recording';
    return new NextResponse(message, { status: 500 });
  }
}

function buildFileOutput(roomName: string): EncodedFileOutput {
  const safeRoom = roomName.replace(/[^a-zA-Z0-9_-]/g, '_');
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
  const filename = `${timestamp}-${safeRoom}.mp4`;

  if (process.env.STORAGE_BACKEND === 'local') {
    const basePath = process.env.RECORDINGS_PATH ?? '/recordings';
    return new EncodedFileOutput({ filepath: `${basePath}/${filename}` });
  }

  const { S3_KEY_ID, S3_KEY_SECRET, S3_BUCKET, S3_REGION, S3_ENDPOINT } = process.env;
  if (!S3_KEY_ID || !S3_KEY_SECRET || !S3_BUCKET || !S3_REGION) {
    throw new Error('Recording requires S3_KEY_ID, S3_KEY_SECRET, S3_BUCKET, and S3_REGION');
  }

  return new EncodedFileOutput({
    filepath: `recordings/${filename}`,
    output: {
      case: 's3',
      value: new S3Upload({
        accessKey: S3_KEY_ID,
        secret: S3_KEY_SECRET,
        bucket: S3_BUCKET,
        region: S3_REGION,
        ...(S3_ENDPOINT ? { endpoint: S3_ENDPOINT } : {}),
      }),
    },
  });
}
