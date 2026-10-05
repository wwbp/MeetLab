'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Label, PageHeader, SectionHeading, Stat, buttonClass, inputClass, secondaryButtonClass, textActionClass } from '@/components/console/swiss';
import { PrepareStudy } from '@/components/desk/prepare-study';
import type {
  ConciergeRoom,
  InviteResponse,
  RoomHealthResponse,
  RoomsResponse,
} from '@/lib/concierge/types';

const POLL_INTERVAL_MS = 5000;

type RoomHealthByName = Record<string, RoomHealthResponse>;

async function requestJson<T>(input: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has('Content-Type') && init?.body) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(input, {
    ...init,
    headers,
    cache: 'no-store',
  });

  if (!response.ok) {
    const text = await response.text();
    let message = text;
    if (text) {
      try {
        const payload = JSON.parse(text) as { error?: string };
        if (typeof payload.error === 'string' && payload.error) {
          message = payload.error;
        }
      } catch {
        // Keep original response text.
      }
    }
    throw new Error(message || `Request failed: ${response.status}`);
  }

  if (response.status === 204) {
    return {} as T;
  }
  return (await response.json()) as T;
}

function readErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unexpected error';
}

function formatTimestamp(value?: string): string {
  if (!value) {
    return 'n/a';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

// The bot's state in plain words (the raw status stays under Details).
const BOT_STATE: Record<string, string> = {
  missing: 'No bot',
  starting: 'Joining…',
  connected_no_tracks: 'In the room, no audio yet',
  connected: 'In the room',
};

function prettyStatus(value: string): string {
  return value.replace(/_/g, ' ');
}

export function ConciergeConsole({ tracesUrl }: { tracesUrl?: string }) {
  const [rooms, setRooms] = useState<ConciergeRoom[]>([]);
  const [roomHealthByName, setRoomHealthByName] = useState<RoomHealthByName>({});

  const [newRoomName, setNewRoomName] = useState('');
  const [createWithBot, setCreateWithBot] = useState(true);

  const [loading, setLoading] = useState(false);
  const [runningAction, setRunningAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const activeRoomCount = useMemo(
    () =>
      rooms.filter((room) => {
        const health = roomHealthByName[room.name];
        if (health) {
          return health.room.status === 'active';
        }
        return (room.numParticipants ?? 0) > 0;
      }).length,
    [roomHealthByName, rooms]
  );

  const connectedBotCount = useMemo(
    () => rooms.filter((room) => roomHealthByName[room.name]?.bot.status === 'connected').length,
    [roomHealthByName, rooms]
  );

  async function loadRoomsAndHealth() {
    setLoading(true);
    try {
      const roomsData = await requestJson<RoomsResponse>('/api/concierge/rooms');
      const sortedRooms = roomsData.rooms.sort((a, b) => a.name.localeCompare(b.name));
      setRooms(sortedRooms);

      const healthResults = await Promise.all(
        sortedRooms.map(async (room) => {
          try {
            const health = await requestJson<RoomHealthResponse>(
              `/api/concierge/rooms/${encodeURIComponent(room.name)}/health`
            );
            return [room.name, health] as const;
          } catch {
            return [room.name, null] as const;
          }
        })
      );

      const nextHealthByName: RoomHealthByName = {};
      for (const [roomName, health] of healthResults) {
        if (health) {
          nextHealthByName[roomName] = health;
        }
      }
      setRoomHealthByName(nextHealthByName);

      setError(null);
    } catch (loadError) {
      setError(readErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }

  async function handleCreateRoom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const roomName = newRoomName.trim();
    if (!roomName) {
      setError('Room name is required');
      return;
    }

    setRunningAction('create-room');
    try {
      await requestJson('/api/concierge/rooms', {
        method: 'POST',
        body: JSON.stringify({ name: roomName }),
      });

      if (createWithBot) {
        await requestJson(`/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`, {
          method: 'POST',
        });
      }

      setNewRoomName('');
      setNotice(
        createWithBot
          ? `Room "${roomName}" created and bot start requested`
          : `Room "${roomName}" created`
      );
      await loadRoomsAndHealth();
      setError(null);
    } catch (actionError) {
      setError(readErrorMessage(actionError));
    } finally {
      setRunningAction(null);
    }
  }

  async function handleDeleteRoom(roomName: string) {
    const confirmed = window.confirm(`Delete room "${roomName}" and disconnect participants?`);
    if (!confirmed) {
      return;
    }

    setRunningAction(`delete-${roomName}`);
    try {
      await requestJson(`/api/concierge/rooms/${encodeURIComponent(roomName)}`, {
        method: 'DELETE',
      });
      setNotice(`Room "${roomName}" deleted`);
      await loadRoomsAndHealth();
      setError(null);
    } catch (actionError) {
      setError(readErrorMessage(actionError));
    } finally {
      setRunningAction(null);
    }
  }

  async function handleStartBot(roomName: string) {
    setRunningAction(`start-bot-${roomName}`);
    try {
      await requestJson(`/api/concierge/rooms/${encodeURIComponent(roomName)}/bots`, {
        method: 'POST',
      });
      setNotice(`Bot start requested for "${roomName}"`);
      await loadRoomsAndHealth();
      setError(null);
    } catch (actionError) {
      setError(readErrorMessage(actionError));
    } finally {
      setRunningAction(null);
    }
  }

  async function handleStopBot(roomName: string, botIdentity?: string) {
    if (!botIdentity) {
      setError('No active bot identity found for this room');
      return;
    }
    const confirmed = window.confirm(`Disconnect bot "${botIdentity}" from "${roomName}"?`);
    if (!confirmed) {
      return;
    }

    setRunningAction(`stop-bot-${roomName}`);
    try {
      await requestJson(
        `/api/concierge/rooms/${encodeURIComponent(roomName)}/bots/${encodeURIComponent(botIdentity)}`,
        { method: 'DELETE' }
      );
      setNotice(`Bot "${botIdentity}" disconnected from "${roomName}"`);
      await loadRoomsAndHealth();
      setError(null);
    } catch (actionError) {
      setError(readErrorMessage(actionError));
    } finally {
      setRunningAction(null);
    }
  }

  async function handleCopyJoinLink(roomName: string) {
    try {
      const data = await requestJson<InviteResponse>(
        `/api/concierge/rooms/${encodeURIComponent(roomName)}/invite`
      );
      await navigator.clipboard.writeText(data.invite.meetJoinUrl);
      setNotice(`Join link copied for "${roomName}"`);
      setError(null);
    } catch (copyError) {
      setError(readErrorMessage(copyError));
    }
  }

  useEffect(() => {
    void loadRoomsAndHealth();
    const interval = window.setInterval(() => {
      void loadRoomsAndHealth();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, []);

  return (
    <div className="mx-auto w-full max-w-6xl px-6 py-10">
      <PageHeader title="Rooms" lead="Each room is one meeting with one bot. Create rooms, start or stop their bots, and share the join link.">
        {tracesUrl && (
          <a href={tracesUrl} target="_blank" rel="noopener noreferrer" className={textActionClass + ' text-muted-foreground'}>
            Traces ↗
          </a>
        )}
      </PageHeader>

      <div className="grid grid-cols-3 gap-8 pb-12">
        <Stat value={rooms.length} label="rooms" />
        <Stat value={activeRoomCount} label="in use" />
        <Stat value={connectedBotCount} label="bots in a room" />
      </div>

      {error && <p className="text-signal pb-6 text-sm">{error}</p>}
      {notice && <p className="pb-6 text-sm">{notice}</p>}

      <div className="space-y-12">
        <section className="space-y-6">
          <SectionHeading n={1} title="Prepare for a study" />
          <div className="pl-12"><PrepareStudy /></div>
        </section>

        <section className="space-y-6">
          <SectionHeading n={2} title="New room" />
          <form onSubmit={handleCreateRoom} className="flex flex-wrap items-end gap-4 pl-12">
            <label className="block w-72 space-y-1.5">
              <Label>Name</Label>
              <input value={newRoomName} onChange={(event) => setNewRoomName(event.target.value)} className={inputClass + ' font-mono'} placeholder="team-sync-1" maxLength={128} />
            </label>
            <label className="flex items-center gap-2 pb-2.5 text-sm">
              <input type="checkbox" checked={createWithBot} onChange={(event) => setCreateWithBot(event.target.checked)} className="accent-foreground h-4 w-4" />
              Start its bot now
            </label>
            <button type="submit" className={buttonClass} disabled={runningAction === 'create-room'}>
              {runningAction === 'create-room' ? 'Creating…' : 'Create room'}
            </button>
          </form>
        </section>

        <section className="space-y-6">
          <SectionHeading n={3} title="Rooms" />
          <div className="pl-12">
            <div className="text-muted-foreground grid grid-cols-[minmax(0,2fr)_6rem_minmax(0,1.5fr)_auto] gap-4 border-b pb-2 text-sm">
              <span>Room</span>
              <span>People</span>
              <span>Bot</span>
              <span className="text-right">{loading ? 'Updating…' : 'Up to date'}</span>
            </div>
            {rooms.length === 0 && <p className="text-muted-foreground py-4 text-sm">No rooms yet.</p>}
            {rooms.map((room) => {
              const health = roomHealthByName[room.name];
              const botIdentity = health?.bot.identity;
              const botAssignedIdentity = health?.bot.assignedIdentity;
              const botIsAssigned = Boolean(botIdentity || botAssignedIdentity);
              const canStartBot = !botIsAssigned;
              return (
                <div key={room.name} className="border-foreground/15 border-b py-4">
                  <div className="grid grid-cols-[minmax(0,2fr)_6rem_minmax(0,1.5fr)_auto] items-center gap-4">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-sm font-medium">{room.name}</p>
                      <p className="text-muted-foreground text-xs">Created {formatTimestamp(health?.room.creationTime ?? room.creationTime)}</p>
                    </div>
                    <p className="text-sm tabular-nums">{health?.room.numParticipants ?? room.numParticipants ?? 0}</p>
                    <p className="text-sm">{BOT_STATE[health?.bot.status ?? 'missing']}</p>
                    <div className="flex items-center justify-end gap-3">
                      {canStartBot ? (
                        <button className={buttonClass + ' px-3 py-1.5'} disabled={runningAction === `start-bot-${room.name}`} onClick={() => handleStartBot(room.name)}>
                          {runningAction === `start-bot-${room.name}` ? 'Starting…' : 'Start bot'}
                        </button>
                      ) : (
                        <button className={secondaryButtonClass + ' px-3 py-1.5'} disabled={!botIdentity || runningAction === `stop-bot-${room.name}`} onClick={() => handleStopBot(room.name, botIdentity)}>
                          {runningAction === `stop-bot-${room.name}` ? 'Stopping…' : 'Stop bot'}
                        </button>
                      )}
                      <button className={textActionClass} onClick={() => handleCopyJoinLink(room.name)}>Copy join link</button>
                      <button className={textActionClass + ' text-signal'} disabled={runningAction === `delete-${room.name}`} onClick={() => handleDeleteRoom(room.name)}>
                        {runningAction === `delete-${room.name}` ? 'Deleting…' : 'Delete'}
                      </button>
                    </div>
                  </div>
                  <details className="pt-2">
                    <summary className="text-muted-foreground hover:text-foreground cursor-pointer text-xs">Details</summary>
                    <dl className="text-muted-foreground grid grid-cols-2 gap-x-6 gap-y-1 pt-2 text-xs sm:grid-cols-4">
                      <dt>Room</dt><dd className="font-mono">{prettyStatus(health?.room.status ?? 'missing')}</dd>
                      <dt>Bot</dt><dd className="font-mono">{prettyStatus(health?.bot.status ?? 'missing')}</dd>
                      <dt>Bot tracks</dt><dd className="font-mono">{health?.bot.trackCount ?? 0}</dd>
                      <dt>Subscription signal</dt><dd className="font-mono">{prettyStatus(health?.bot.subscriptionSignal.status ?? 'unknown')}</dd>
                      <dt>Bot identity</dt><dd className="col-span-3 truncate font-mono">{botIdentity ?? botAssignedIdentity ?? '–'}</dd>
                    </dl>
                  </details>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}
