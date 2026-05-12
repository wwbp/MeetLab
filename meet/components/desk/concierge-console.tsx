'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
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

function prettyStatus(value: string): string {
  return value.replace(/_/g, ' ');
}

export function ConciergeConsole() {
  const [rooms, setRooms] = useState<ConciergeRoom[]>([]);
  const [roomHealthByName, setRoomHealthByName] = useState<RoomHealthByName>({});
  const [roomMetadataDrafts, setRoomMetadataDrafts] = useState<Record<string, string>>({});

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

      setRoomMetadataDrafts((current) => {
        const next = { ...current };
        for (const room of sortedRooms) {
          if (next[room.name] === undefined) {
            next[room.name] = room.metadata ?? '';
          }
        }
        for (const roomName of Object.keys(next)) {
          if (!sortedRooms.some((room) => room.name === roomName)) {
            delete next[roomName];
          }
        }
        return next;
      });

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

  async function handleUpdateRoom(roomName: string) {
    setRunningAction(`update-${roomName}`);
    try {
      await requestJson(`/api/concierge/rooms/${encodeURIComponent(roomName)}`, {
        method: 'PATCH',
        body: JSON.stringify({ metadata: roomMetadataDrafts[roomName] ?? '' }),
      });
      setNotice(`Room "${roomName}" updated`);
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
    <div className="bg-background min-h-svh">
      <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6 sm:px-8 sm:py-10">
        <header className="space-y-2">
          <p className="text-muted-foreground font-mono text-xs uppercase">Desk</p>
          <h1 className="text-3xl font-medium">Rooms and Bot Ops</h1>
          <p className="text-muted-foreground text-sm">
            Minimal control plane: create or update rooms, run one bot per room, share join links,
            and watch room and bot health.
          </p>
        </header>

        <section className="grid gap-3 sm:grid-cols-3">
          <div className="border-foreground/20 border p-3">
            <p className="text-muted-foreground font-mono text-[11px] uppercase">rooms</p>
            <p className="text-2xl">{rooms.length}</p>
          </div>
          <div className="border-foreground/20 border p-3">
            <p className="text-muted-foreground font-mono text-[11px] uppercase">active rooms</p>
            <p className="text-2xl">{activeRoomCount}</p>
          </div>
          <div className="border-foreground/20 border p-3">
            <p className="text-muted-foreground font-mono text-[11px] uppercase">connected bots</p>
            <p className="text-2xl">{connectedBotCount}</p>
          </div>
        </section>

        {error && (
          <div className="border-destructive text-destructive border p-3 text-sm">{error}</div>
        )}
        {notice && (
          <div className="border-foreground/20 text-foreground border p-3 text-sm">{notice}</div>
        )}

        <section className="border-foreground/20 space-y-3 border p-4">
          <h2 className="text-lg font-medium">Create Room</h2>
          <form onSubmit={handleCreateRoom} className="space-y-3">
            <input
              value={newRoomName}
              onChange={(event) => setNewRoomName(event.target.value)}
              className="border-foreground/20 focus:border-foreground/50 w-full border bg-transparent px-3 py-2 text-sm outline-none"
              placeholder="team-sync-1"
              maxLength={128}
            />
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={createWithBot}
                onChange={(event) => setCreateWithBot(event.target.checked)}
              />
              Start one bot immediately
            </label>
            <Button type="submit" variant="primary" disabled={runningAction === 'create-room'}>
              {runningAction === 'create-room' ? 'Creating...' : 'Create Room'}
            </Button>
          </form>
        </section>

        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-medium">Room Controls</h2>
            <p className="text-muted-foreground text-xs">{loading ? 'syncing...' : 'synced'}</p>
          </div>

          {rooms.length === 0 && (
            <div className="border-foreground/20 text-muted-foreground border p-4 text-sm">
              No rooms yet.
            </div>
          )}

          {rooms.map((room) => {
            const health = roomHealthByName[room.name];
            const botIdentity = health?.bot.identity;
            const botAssignedIdentity = health?.bot.assignedIdentity;
            const botTrackedIdentity = botIdentity ?? botAssignedIdentity ?? '-';
            const botIsAssigned = Boolean(botIdentity || botAssignedIdentity);
            const canStartBot = !botIsAssigned;

            return (
              <article key={room.name} className="border-foreground/20 space-y-3 border p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="font-mono text-base">{room.name}</h3>
                    <p className="text-muted-foreground text-xs">
                      created {formatTimestamp(health?.room.creationTime ?? room.creationTime)}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => handleCopyJoinLink(room.name)}
                  >
                    Copy Join Link
                  </Button>
                </div>

                <div className="grid gap-2 text-sm sm:grid-cols-5">
                  <p>
                    participants:{' '}
                    <span className="font-mono">
                      {health?.room.numParticipants ?? room.numParticipants ?? 0}
                    </span>
                  </p>
                  <p>
                    room health:{' '}
                    <span className="font-mono">
                      {prettyStatus(health?.room.status ?? 'missing')}
                    </span>
                  </p>
                  <p>
                    bot health:{' '}
                    <span className="font-mono">
                      {prettyStatus(health?.bot.status ?? 'missing')}
                    </span>
                  </p>
                  <p>
                    bot tracks: <span className="font-mono">{health?.bot.trackCount ?? 0}</span>
                  </p>
                  <p>
                    bot sub signal:{' '}
                    <span className="font-mono">
                      {prettyStatus(health?.bot.subscriptionSignal.status ?? 'unknown')}
                    </span>
                  </p>
                </div>

                <p className="text-muted-foreground text-xs">
                  bot identity: <span className="font-mono">{botTrackedIdentity}</span>
                </p>

                <div className="space-y-2">
                  <label className="text-muted-foreground block text-xs">Room metadata</label>
                  <input
                    value={roomMetadataDrafts[room.name] ?? ''}
                    onChange={(event) =>
                      setRoomMetadataDrafts((current) => ({
                        ...current,
                        [room.name]: event.target.value,
                      }))
                    }
                    className="border-foreground/20 focus:border-foreground/50 w-full border bg-transparent px-3 py-2 text-sm outline-none"
                    placeholder="optional metadata"
                  />
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={runningAction === `update-${room.name}`}
                    onClick={() => handleUpdateRoom(room.name)}
                  >
                    {runningAction === `update-${room.name}` ? 'Saving...' : 'Save Room'}
                  </Button>
                  <Button
                    size="sm"
                    variant="primary"
                    disabled={!canStartBot || runningAction === `start-bot-${room.name}`}
                    onClick={() => handleStartBot(room.name)}
                  >
                    {runningAction === `start-bot-${room.name}`
                      ? 'Starting...'
                      : canStartBot
                        ? 'Start Bot'
                        : 'Bot Assigned'}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!botIdentity || runningAction === `stop-bot-${room.name}`}
                    onClick={() => handleStopBot(room.name, botIdentity)}
                  >
                    {runningAction === `stop-bot-${room.name}` ? 'Stopping...' : 'Stop Bot'}
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={runningAction === `delete-${room.name}`}
                    onClick={() => handleDeleteRoom(room.name)}
                  >
                    {runningAction === `delete-${room.name}` ? 'Deleting...' : 'Delete Room'}
                  </Button>
                </div>
              </article>
            );
          })}
        </section>
      </div>
    </div>
  );
}
