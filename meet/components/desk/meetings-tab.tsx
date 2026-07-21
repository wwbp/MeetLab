'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import type { ConversationRecord, ConversationsResponse, MediaFileRecord } from '@/lib/concierge/types';

const POLL_INTERVAL_MS = 10_000;
const PAGE_SIZE = 10;

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function formatDuration(startedAt: string, endedAt: string | null): string {
  if (!endedAt) return 'live';
  const secs = Math.round((new Date(endedAt).getTime() - new Date(startedAt).getTime()) / 1000);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function audioTrackLabel(track: MediaFileRecord): string {
  const raw = typeof track.meta.speaker_id === 'string' ? track.meta.speaker_id : '';
  const name = raw.split('__')[0] || 'audio';
  const part = typeof track.meta.part === 'number' ? track.meta.part : 0;
  return part > 0 ? `Audio: ${name} (${part + 1})` : `Audio: ${name}`;
}

function FileActions({
  conv,
  onGenerate,
  onDownload,
}: {
  conv: ConversationRecord;
  onGenerate: (id: string) => void;
  onDownload: (convId: string, fileId: string) => void;
}) {
  const recording = conv.media_files.find((f) => f.type === 'recording');
  const transcript = conv.media_files.find((f) => f.type === 'transcript');
  const audioTracks = conv.media_files
    .filter((f) => f.type === 'audio_track' && f.status === 'available')
    .sort((a, b) => audioTrackLabel(a).localeCompare(audioTrackLabel(b)));
  const isRunning = conv.status === 'running';

  return (
    <div className="flex flex-wrap gap-1.5">
      {/* Recording */}
      {recording?.status === 'available' && (
        <Button variant="outline" size="sm" onClick={() => onDownload(conv.id, recording.id)}>
          Video
        </Button>
      )}
      {recording?.status === 'pending' && (
        <span className="text-xs text-muted-foreground animate-pulse self-center">recording…</span>
      )}
      {recording?.status === 'failed' && (
        <span className="text-xs text-destructive self-center">rec failed</span>
      )}

      {/* Transcript */}
      {transcript?.status === 'available' && (
        <Button variant="outline" size="sm" onClick={() => onDownload(conv.id, transcript.id)}>
          Transcript
        </Button>
      )}
      {transcript?.status === 'pending' && (
        <span className="text-xs text-muted-foreground animate-pulse self-center">generating…</span>
      )}
      {transcript?.status === 'failed' && (
        <span className="text-xs text-destructive self-center">tx failed</span>
      )}
      {!transcript && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onGenerate(conv.id)}
          disabled={isRunning}
          title={isRunning ? 'Session still running' : 'Generate transcript'}
          className="text-muted-foreground"
        >
          + Transcript
        </Button>
      )}

      {/* Per-speaker audio tracks (source-separated WAV) */}
      {audioTracks.map((track) => (
        <Button
          key={track.id}
          variant="outline"
          size="sm"
          onClick={() => onDownload(conv.id, track.id)}
          title="Download this speaker's audio track (WAV)"
        >
          {audioTrackLabel(track)}
        </Button>
      ))}
    </div>
  );
}

export function MeetingsTab() {
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const offset = page * PAGE_SIZE;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  const fetchConversations = useCallback(async () => {
    try {
      const res = await fetch(`/api/meetings?limit=${PAGE_SIZE}&offset=${offset}`, { cache: 'no-store' });
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

  // Fast-poll while anything is generating
  const hasPending = conversations.some((c) => c.media_files.some((f) => f.status === 'pending'));
  useEffect(() => {
    if (!hasPending) return;
    const id = setInterval(() => void fetchConversations(), 3000);
    return () => clearInterval(id);
  }, [hasPending, fetchConversations]);

  // Auto-reconcile recordings stuck in pending >90s (webhook may have been missed)
  const hasStuckRecording = conversations.some((c) =>
    c.media_files.some(
      (f) =>
        f.type === 'recording' &&
        f.status === 'pending' &&
        Date.now() - new Date(f.created_at).getTime() > 90_000,
    ),
  );
  useEffect(() => {
    if (!hasStuckRecording) return;
    fetch('/api/meetings/reconcile', { method: 'POST' }).catch(() => {});
  }, [hasStuckRecording]);

  return (
    <div className="p-6 space-y-4 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Meetings</h1>
        <span className="text-muted-foreground text-sm">{total} total</span>
      </div>

      {/* Errors */}
      {(error || actionError) && (
        <div className="text-destructive text-sm border border-destructive/30 rounded px-3 py-2">
          {error ?? actionError}
        </div>
      )}

      {/* Content */}
      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : conversations.length === 0 ? (
        <p className="text-muted-foreground text-sm">No meetings recorded yet.</p>
      ) : (
        <>
          {/* Card grid */}
          <div className="grid gap-2">
            {conversations.map((conv) => (
              <div
                key={conv.id}
                className="flex items-center gap-4 rounded border px-4 py-3 hover:bg-muted/10 transition-colors"
              >
                {/* Room + date */}
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-sm truncate">{conv.room_name}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {formatDate(conv.started_at)}
                    {' · '}
                    {formatDuration(conv.started_at, conv.ended_at)}
                    {' · '}
                    {conv.utterance_count} turns
                  </p>
                </div>

                {/* Status badge */}
                <span
                  className={`shrink-0 text-xs px-2 py-0.5 rounded-full ${
                    conv.status === 'running'
                      ? 'bg-green-100 text-green-800'
                      : conv.status === 'error'
                        ? 'bg-red-100 text-red-800'
                        : 'bg-muted text-muted-foreground'
                  }`}
                >
                  {conv.status === 'running' ? 'live' : conv.status}
                </span>

                {/* Actions */}
                <FileActions
                  conv={conv}
                  onGenerate={handleGenerate}
                  onDownload={handleDownload}
                />
              </div>
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-1">
              <Button
                variant="outline"
                size="sm"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
              >
                ← Previous
              </Button>
              <span className="text-muted-foreground text-xs">
                Page {page + 1} of {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages - 1}
                onClick={() => setPage(page + 1)}
              >
                Next →
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
