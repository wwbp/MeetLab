'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import type { ConversationRecord, ConversationsResponse, MediaFileRecord } from '@/lib/concierge/types';

const POLL_INTERVAL_MS = 10_000;

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

function formatDuration(startedAt: string, endedAt: string | null): string {
  if (!endedAt) return 'ongoing';
  const secs = Math.round((new Date(endedAt).getTime() - new Date(startedAt).getTime()) / 1000);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function FileCell({
  conv,
  type,
  onGenerate,
  onDownload,
}: {
  conv: ConversationRecord;
  type: 'recording' | 'transcript';
  onGenerate: (id: string) => void;
  onDownload: (convId: string, fileId: string) => void;
}) {
  const file = conv.media_files.find((f) => f.type === type);

  if (!file) {
    if (type === 'transcript') {
      return (
        <Button
          variant="outline"
          size="sm"
          onClick={() => onGenerate(conv.id)}
          disabled={conv.status === 'running'}
          title={conv.status === 'running' ? 'Session still running — generate after it ends' : undefined}
        >
          Generate
        </Button>
      );
    }
    return <span className="text-muted-foreground text-xs">—</span>;
  }

  if (file.status === 'pending') {
    return <span className="text-muted-foreground text-xs animate-pulse">generating…</span>;
  }
  if (file.status === 'failed') {
    return <span className="text-destructive text-xs">failed</span>;
  }
  // available
  return (
    <Button variant="outline" size="sm" onClick={() => onDownload(conv.id, file.id)}>
      Download
    </Button>
  );
}

export function MeetingsTab() {
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const limit = 50;

  const fetchConversations = useCallback(async () => {
    try {
      const res = await fetch(`/api/meetings?limit=${limit}&offset=${offset}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as ConversationsResponse;
      setConversations(data.conversations);
      setTotal(data.total);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load meetings');
    } finally {
      setLoading(false);
    }
  }, [offset]);

  useEffect(() => {
    void fetchConversations();
    const id = setInterval(() => void fetchConversations(), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchConversations]);

  const handleGenerate = async (convId: string) => {
    setActionError(null);
    try {
      const res = await fetch(`/api/meetings/${convId}/transcript`, { method: 'POST' });
      if (!res.ok && res.status !== 409) {
        const data = (await res.json()) as { error?: string };
        throw new Error(data.error ?? 'Failed to queue transcript');
      }
      await fetchConversations();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to queue transcript');
    }
  };

  const handleDownload = (convId: string, fileId: string) => {
    window.open(`/api/meetings/${convId}/files/${fileId}/download`, '_blank');
  };

  const hasPending = conversations.some((c) =>
    c.media_files.some((f) => f.status === 'pending'),
  );

  // Poll faster when files are generating
  useEffect(() => {
    if (!hasPending) return;
    const id = setInterval(() => void fetchConversations(), 3000);
    return () => clearInterval(id);
  }, [hasPending, fetchConversations]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Meetings</h1>
        <span className="text-muted-foreground text-sm">{total} total</span>
      </div>

      {error && (
        <div className="text-destructive text-sm border border-destructive/30 rounded px-3 py-2">
          {error}
        </div>
      )}
      {actionError && (
        <div className="text-destructive text-sm border border-destructive/30 rounded px-3 py-2">
          {actionError}
        </div>
      )}

      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : conversations.length === 0 ? (
        <p className="text-muted-foreground text-sm">No meetings recorded yet.</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/40">
                  <th className="px-4 py-2 text-left font-medium">Room</th>
                  <th className="px-4 py-2 text-left font-medium">Started</th>
                  <th className="px-4 py-2 text-left font-medium">Duration</th>
                  <th className="px-4 py-2 text-left font-medium">Turns</th>
                  <th className="px-4 py-2 text-left font-medium">Status</th>
                  <th className="px-4 py-2 text-left font-medium">Recording</th>
                  <th className="px-4 py-2 text-left font-medium">Transcript</th>
                </tr>
              </thead>
              <tbody>
                {conversations.map((conv) => (
                  <tr key={conv.id} className="border-b last:border-0 hover:bg-muted/20">
                    <td className="px-4 py-2 font-mono text-xs">{conv.room_name}</td>
                    <td className="px-4 py-2 text-xs">{formatDate(conv.started_at)}</td>
                    <td className="px-4 py-2 text-xs">{formatDuration(conv.started_at, conv.ended_at)}</td>
                    <td className="px-4 py-2 text-xs">{conv.utterance_count}</td>
                    <td className="px-4 py-2">
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full ${
                          conv.status === 'running'
                            ? 'bg-green-100 text-green-800'
                            : conv.status === 'error'
                              ? 'bg-red-100 text-red-800'
                              : 'bg-muted text-muted-foreground'
                        }`}
                      >
                        {conv.status}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <FileCell
                        conv={conv}
                        type="recording"
                        onGenerate={handleGenerate}
                        onDownload={handleDownload}
                      />
                    </td>
                    <td className="px-4 py-2">
                      <FileCell
                        conv={conv}
                        type="transcript"
                        onGenerate={handleGenerate}
                        onDownload={handleDownload}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {total > limit && (
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - limit))}
              >
                Previous
              </Button>
              <span className="text-muted-foreground text-xs">
                {offset + 1}–{Math.min(offset + limit, total)} of {total}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + limit >= total}
                onClick={() => setOffset(offset + limit)}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
