import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NextRequest } from 'next/server';
import { GET as listMeetings } from './route';
import { POST as queueTranscript } from './[id]/transcript/route';
import { GET as downloadFile } from './[id]/files/[fileId]/download/route';

const mockFetch = vi.hoisted(() => vi.fn());
const mockGetServerConfig = vi.hoisted(() => vi.fn());

vi.stubGlobal('fetch', mockFetch);

vi.mock('@/lib/config/server', () => ({
  getServerConfig: mockGetServerConfig,
}));

const DEFAULT_CONFIG = {
  botRunnerUrl: 'http://agent-runner:7860/',
  botRunnerSecret: 'test-secret',
};

function jsonRes(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// ---------------------------------------------------------------------------
// GET /api/meetings
// ---------------------------------------------------------------------------
describe('GET /api/meetings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(DEFAULT_CONFIG);
  });

  it('returns 500 when botRunnerUrl is missing', async () => {
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: undefined });
    const req = new NextRequest('http://localhost/api/meetings');
    const res = await listMeetings(req);
    expect(res.status).toBe(500);
  });

  it('proxies to agent-runner conversations endpoint', async () => {
    mockFetch.mockResolvedValue(jsonRes({ conversations: [], total: 0, limit: 50, offset: 0 }));
    const req = new NextRequest('http://localhost/api/meetings?limit=10&offset=0');
    await listMeetings(req);
    expect(mockFetch).toHaveBeenCalledWith(
      'http://agent-runner:7860/conversations?limit=10&offset=0',
      expect.objectContaining({ cache: 'no-store' }),
    );
  });

  it('forwards Authorization header', async () => {
    mockFetch.mockResolvedValue(jsonRes({ conversations: [], total: 0, limit: 50, offset: 0 }));
    const req = new NextRequest('http://localhost/api/meetings');
    await listMeetings(req);
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer test-secret');
  });

  it('returns parsed JSON from agent-runner', async () => {
    const payload = { conversations: [{ id: 'c1', room_name: 'r1' }], total: 1, limit: 50, offset: 0 };
    mockFetch.mockResolvedValue(jsonRes(payload));
    const req = new NextRequest('http://localhost/api/meetings');
    const res = await listMeetings(req);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.conversations).toHaveLength(1);
  });

  it('returns error when agent-runner call fails', async () => {
    mockFetch.mockRejectedValue(new Error('connection failed'));
    const req = new NextRequest('http://localhost/api/meetings');
    const res = await listMeetings(req);
    expect(res.status).toBe(500);
  });
});

// ---------------------------------------------------------------------------
// POST /api/meetings/[id]/transcript
// ---------------------------------------------------------------------------
describe('POST /api/meetings/[id]/transcript', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(DEFAULT_CONFIG);
  });

  async function callQueueTranscript(id: string) {
    const req = new NextRequest(`http://localhost/api/meetings/${id}/transcript`, { method: 'POST' });
    return queueTranscript(req, { params: Promise.resolve({ id }) });
  }

  it('returns 500 when botRunnerUrl is missing', async () => {
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: undefined });
    const res = await callQueueTranscript('conv-123');
    expect(res.status).toBe(500);
  });

  it('proxies POST to agent-runner conversations/{id}/transcript', async () => {
    mockFetch.mockResolvedValue(jsonRes({ media_file_id: 'mf-1', status: 'pending' }));
    await callQueueTranscript('conv-abc');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://agent-runner:7860/conversations/conv-abc/transcript',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('returns 202 / pending response from agent-runner', async () => {
    mockFetch.mockResolvedValue(jsonRes({ media_file_id: 'mf-1', status: 'pending' }));
    const res = await callQueueTranscript('conv-1');
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.status).toBe('pending');
  });

  it('passes through 409 when transcript already exists', async () => {
    mockFetch.mockResolvedValue(jsonRes({ error: 'already exists', media_file_id: 'mf-1' }, 409));
    const res = await callQueueTranscript('conv-1');
    expect(res.status).toBe(409);
  });
});

// ---------------------------------------------------------------------------
// GET /api/meetings/[id]/files/[fileId]/download
// ---------------------------------------------------------------------------
describe('GET /api/meetings/[id]/files/[fileId]/download', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetServerConfig.mockReturnValue(DEFAULT_CONFIG);
  });

  async function callDownload(id: string, fileId: string) {
    const req = new NextRequest(
      `http://localhost/api/meetings/${id}/files/${fileId}/download`,
    );
    return downloadFile(req, { params: Promise.resolve({ id, fileId }) });
  }

  it('returns 500 when botRunnerUrl is missing', async () => {
    mockGetServerConfig.mockReturnValue({ botRunnerUrl: undefined });
    const res = await callDownload('conv-1', 'file-1');
    expect(res.status).toBe(500);
  });

  it('proxies GET to agent-runner media-files/{fileId}/download', async () => {
    const body = new ReadableStream();
    mockFetch.mockResolvedValue(
      new Response(body, { status: 200, headers: { 'Content-Type': 'text/markdown' } }),
    );
    await callDownload('conv-1', 'mf-abc');
    const [url] = mockFetch.mock.calls[0] as [string];
    expect(url).toBe('http://agent-runner:7860/media-files/mf-abc/download');
  });

  it('follows S3 redirect through', async () => {
    mockFetch.mockResolvedValue(
      new Response(null, { status: 302, headers: { location: 'https://s3.example.com/presigned' } }),
    );
    const res = await callDownload('conv-1', 'mf-1');
    expect(res.status).toBe(302);
  });

  it('passes through error status from agent-runner', async () => {
    mockFetch.mockResolvedValue(jsonRes({ error: 'file not ready' }, 409));
    const res = await callDownload('conv-1', 'mf-1');
    expect(res.status).toBe(409);
  });
});
