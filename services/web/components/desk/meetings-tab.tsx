'use client';

import { useCallback, useEffect, useState } from 'react';
import { PageHeader, Stat, secondaryButtonClass, textActionClass } from '@/components/console/swiss';
import type { ConversationRecord, ConversationsResponse } from '@/lib/concierge/types';

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
  const audioTrackCount = conv.media_files.filter(
    (f) => f.type === 'audio_track' && f.status === 'available',
  ).length;
  const isRunning = conv.status === 'running';

  return (
    <div className="flex flex-wrap justify-end gap-x-4 gap-y-1">
      {recording?.status === 'available' && (
        <button className={textActionClass} onClick={() => onDownload(conv.id, recording.id)}>Video</button>
      )}
      {recording?.status === 'pending' && <span className="text-muted-foreground animate-pulse text-sm">Recording…</span>}
      {recording?.status === 'failed' && <span className="text-signal text-sm">Video failed</span>}

      {transcript?.status === 'available' && (
        <button className={textActionClass} onClick={() => onDownload(conv.id, transcript.id)}>Transcript</button>
      )}
      {transcript?.status === 'pending' && <span className="text-muted-foreground animate-pulse text-sm">Making transcript…</span>}
      {transcript?.status === 'failed' && <span className="text-signal text-sm">Transcript failed</span>}
      {!transcript && (
        <button
          className={textActionClass + ' text-muted-foreground'}
          onClick={() => onGenerate(conv.id)}
          disabled={isRunning}
          title={isRunning ? 'Available when the meeting ends' : 'Make a transcript'}
        >
          Make transcript
        </button>
      )}

      {/* Per-speaker audio tracks (source-separated WAV), bundled into one zip */}
      {audioTrackCount > 0 && (
        <button
          className={textActionClass}
          onClick={() => window.open(`/api/meetings/${conv.id}/audio-tracks/download`, '_blank')}
          title="Each speaker's own audio, as a zip"
        >
          Audio ({audioTrackCount})
        </button>
      )}
    </div>
  );
}

// A meeting's status in plain words.
const MEETING_STATE: Record<string, string> = { running: 'Live', completed: 'Ended', ended: 'Room closed', error: 'Ended with an error' };

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
    <div className="mx-auto w-full max-w-6xl px-6 py-10">
      <PageHeader title="Meetings" lead="Every meeting a bot was in, newest first, with its recordings and transcript." />
      <div className="pb-12"><Stat value={total} label="meetings" /></div>

      {(error || actionError) && <p className="text-signal pb-6 text-sm">{error ?? actionError}</p>}

      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : conversations.length === 0 ? (
        <p className="text-muted-foreground text-sm">No meetings yet.</p>
      ) : (
        <>
          <div className="text-muted-foreground grid grid-cols-[minmax(0,2fr)_5rem_4rem_10rem_minmax(0,2fr)] gap-4 border-b pb-2 text-sm">
            <span>Meeting</span>
            <span>Length</span>
            <span>Turns</span>
            <span>Status</span>
            <span className="text-right">Files</span>
          </div>
          {conversations.map((conv) => (
            <div key={conv.id} className="border-foreground/15 grid grid-cols-[minmax(0,2fr)_5rem_4rem_10rem_minmax(0,2fr)] items-center gap-4 border-b py-3">
              <div className="min-w-0">
                <p className="truncate font-mono text-sm font-medium">{conv.room_name}</p>
                <p className="text-muted-foreground text-xs">{formatDate(conv.started_at)}</p>
              </div>
              <p className="text-sm tabular-nums">{formatDuration(conv.started_at, conv.ended_at)}</p>
              <p className="text-sm tabular-nums">{conv.utterance_count}</p>
              <p className={`text-sm ${conv.status === 'error' ? 'text-signal' : conv.status === 'running' ? 'font-medium' : 'text-muted-foreground'}`}>
                {MEETING_STATE[conv.status] ?? conv.status}
              </p>
              <FileActions conv={conv} onGenerate={handleGenerate} onDownload={handleDownload} />
            </div>
          ))}

          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-6">
              <button className={secondaryButtonClass} disabled={page === 0} onClick={() => setPage(page - 1)}>← Newer</button>
              <span className="text-muted-foreground text-sm">Page {page + 1} of {totalPages}</span>
              <button className={secondaryButtonClass} disabled={page >= totalPages - 1} onClick={() => setPage(page + 1)}>Older →</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
