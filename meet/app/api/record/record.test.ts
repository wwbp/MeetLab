import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NextRequest } from 'next/server';
import { EncodedFileOutput, S3Upload } from 'livekit-server-sdk';
import { GET as startRecording } from './start/route';
import { GET as stopRecording } from './stop/route';

const mockListEgress = vi.hoisted(() => vi.fn());
const mockStartRoomCompositeEgress = vi.hoisted(() => vi.fn());
const mockStopEgress = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());

vi.mock('livekit-server-sdk', () => ({
  EgressClient: vi.fn(() => ({
    listEgress: mockListEgress,
    startRoomCompositeEgress: mockStartRoomCompositeEgress,
    stopEgress: mockStopEgress,
  })),
  EncodedFileOutput: vi.fn(),
  S3Upload: vi.fn(),
}));

vi.mock('@/lib/config/server', () => ({
  getServerConfig: mockGetServerConfig,
}));

const DEFAULT_CONFIG = {
  livekitInternalUrl: 'ws://transport-server:7880',
  livekitApiKey: 'devkey',
  livekitApiSecret: 'secret',
};

const activeEgress = { egressId: 'egr-1', status: 1 };
const completedEgress = { egressId: 'egr-2', status: 2 };

function startReq(roomName?: string): NextRequest {
  const url = roomName
    ? `http://localhost/api/record/start?roomName=${encodeURIComponent(roomName)}`
    : 'http://localhost/api/record/start';
  return new NextRequest(url);
}

function stopReq(roomName?: string): NextRequest {
  const url = roomName
    ? `http://localhost/api/record/stop?roomName=${encodeURIComponent(roomName)}`
    : 'http://localhost/api/record/stop';
  return new NextRequest(url);
}

// ---------------------------------------------------------------------------
// /api/record/start
// ---------------------------------------------------------------------------
describe('GET /api/record/start', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(DEFAULT_CONFIG);
    mockListEgress.mockResolvedValue([]);
    mockStartRoomCompositeEgress.mockResolvedValue({});
    process.env.STORAGE_BACKEND = 'local';
    process.env.RECORDINGS_PATH = '/recordings';
    delete process.env.S3_KEY_ID;
    delete process.env.S3_KEY_SECRET;
    delete process.env.S3_BUCKET;
    delete process.env.S3_REGION;
    delete process.env.S3_ENDPOINT;
  });

  it('returns 400 when roomName is missing', async () => {
    const res = await startRecording(startReq());
    expect(res.status).toBe(400);
  });

  it('returns 500 when livekitInternalUrl is not configured', async () => {
    mockGetServerConfig.mockReturnValue({
      livekitInternalUrl: undefined,
      livekitApiKey: 'k',
      livekitApiSecret: 's',
    });
    const res = await startRecording(startReq('room-1'));
    expect(res.status).toBe(500);
  });

  it('returns 409 when room is already being recorded', async () => {
    mockListEgress.mockResolvedValue([activeEgress]);
    const res = await startRecording(startReq('room-1'));
    expect(res.status).toBe(409);
  });

  it('returns 200 and writes to /recordings for local storage', async () => {
    const res = await startRecording(startReq('room-1'));
    expect(res.status).toBe(200);
    const [arg] = vi.mocked(EncodedFileOutput).mock.calls[0] as [{ filepath: string }];
    expect(arg.filepath).toMatch(/^\/recordings\//);
  });

  it('uses custom RECORDINGS_PATH env var', async () => {
    process.env.RECORDINGS_PATH = '/mnt/data';
    await startRecording(startReq('room-1'));
    const [arg] = vi.mocked(EncodedFileOutput).mock.calls[0] as [{ filepath: string }];
    expect(arg.filepath).toMatch(/^\/mnt\/data\//);
  });

  it('filename has no colons or dots before the .mp4 extension', async () => {
    await startRecording(startReq('room-1'));
    const [arg] = vi.mocked(EncodedFileOutput).mock.calls[0] as [{ filepath: string }];
    const basename = arg.filepath.split('/').pop()!;
    expect(basename.slice(0, -4)).not.toMatch(/[:.]/);
  });

  it('starts egress with speaker layout', async () => {
    await startRecording(startReq('room-1'));
    expect(mockStartRoomCompositeEgress).toHaveBeenCalledWith(
      'room-1',
      expect.any(Object),
      expect.objectContaining({ layout: 'speaker' }),
    );
  });

  it('returns 500 with S3 backend when vars are missing', async () => {
    process.env.STORAGE_BACKEND = 's3';
    const res = await startRecording(startReq('room-1'));
    expect(res.status).toBe(500);
    expect(await res.text()).toMatch(/S3/);
  });

  it('returns 200 and configures S3Upload when all S3 vars are set', async () => {
    process.env.STORAGE_BACKEND = 's3';
    process.env.S3_KEY_ID = 'AKID';
    process.env.S3_KEY_SECRET = 'sEcReT';
    process.env.S3_BUCKET = 'meetlab-data';
    process.env.S3_REGION = 'us-east-1';
    const res = await startRecording(startReq('room-1'));
    expect(res.status).toBe(200);
    expect(vi.mocked(S3Upload)).toHaveBeenCalledWith(
      expect.objectContaining({ accessKey: 'AKID', bucket: 'meetlab-data', region: 'us-east-1' }),
    );
  });

  it('returns 500 when EgressClient throws', async () => {
    mockListEgress.mockRejectedValue(new Error('connection refused'));
    const res = await startRecording(startReq('room-1'));
    expect(res.status).toBe(500);
  });
});

// ---------------------------------------------------------------------------
// /api/record/stop
// ---------------------------------------------------------------------------
describe('GET /api/record/stop', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(DEFAULT_CONFIG);
    mockListEgress.mockResolvedValue([]);
    mockStopEgress.mockResolvedValue({});
  });

  it('returns 400 when roomName is missing', async () => {
    const res = await stopRecording(stopReq());
    expect(res.status).toBe(400);
  });

  it('returns 500 when livekitInternalUrl is not configured', async () => {
    mockGetServerConfig.mockReturnValue({
      livekitInternalUrl: undefined,
      livekitApiKey: 'k',
      livekitApiSecret: 's',
    });
    const res = await stopRecording(stopReq('room-1'));
    expect(res.status).toBe(500);
  });

  it('returns 404 when there are no egresses', async () => {
    mockListEgress.mockResolvedValue([]);
    const res = await stopRecording(stopReq('room-1'));
    expect(res.status).toBe(404);
  });

  it('returns 404 when all egresses are already completed', async () => {
    mockListEgress.mockResolvedValue([completedEgress]);
    const res = await stopRecording(stopReq('room-1'));
    expect(res.status).toBe(404);
  });

  it('stops all active egresses and returns 200', async () => {
    mockListEgress.mockResolvedValue([activeEgress, { egressId: 'egr-3', status: 0 }]);
    const res = await stopRecording(stopReq('room-1'));
    expect(res.status).toBe(200);
    expect(mockStopEgress).toHaveBeenCalledTimes(2);
    expect(mockStopEgress).toHaveBeenCalledWith('egr-1');
    expect(mockStopEgress).toHaveBeenCalledWith('egr-3');
  });

  it('ignores completed egresses when stopping', async () => {
    mockListEgress.mockResolvedValue([activeEgress, completedEgress]);
    const res = await stopRecording(stopReq('room-1'));
    expect(res.status).toBe(200);
    expect(mockStopEgress).toHaveBeenCalledTimes(1);
    expect(mockStopEgress).toHaveBeenCalledWith('egr-1');
  });

  it('returns 500 when listEgress throws', async () => {
    mockListEgress.mockRejectedValue(new Error('network error'));
    const res = await stopRecording(stopReq('room-1'));
    expect(res.status).toBe(500);
  });
});
