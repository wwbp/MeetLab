import { NextResponse } from 'next/server';
import { pushConciergeEvent } from '@/lib/concierge/events-store';
import { noStoreHeaders } from '@/lib/concierge/http-utils';
import { getRoomServiceClient, mapRoom } from '@/lib/concierge/livekit-admin';

export const dynamic = 'force-dynamic';
const ROOM_NAME_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/;

function badRequest(message: string) {
  return NextResponse.json({ error: message }, { status: 400, headers: noStoreHeaders() });
}

export async function GET() {
  try {
    const roomService = getRoomServiceClient();
    const rooms = await roomService.listRooms();
    const mappedRooms = rooms.map(mapRoom).sort((a, b) => a.name.localeCompare(b.name));
    return NextResponse.json({ rooms: mappedRooms }, { headers: noStoreHeaders() });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to list rooms';
    return NextResponse.json({ error: message }, { status: 500, headers: noStoreHeaders() });
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json().catch(() => ({}));
    const roomName = typeof body.name === 'string' ? body.name.trim() : '';

    if (!roomName) {
      return badRequest('Room name is required');
    }
    if (!ROOM_NAME_PATTERN.test(roomName)) {
      return badRequest(
        'Room name must be 1-128 characters and use letters, numbers, "_" or "-" only'
      );
    }

    const roomService = getRoomServiceClient();
    const existingRooms = await roomService.listRooms();
    if (existingRooms.some((room) => room.name === roomName)) {
      return NextResponse.json(
        { error: `Room "${roomName}" already exists` },
        { status: 409, headers: noStoreHeaders() }
      );
    }

    const room = await roomService.createRoom({ name: roomName });

    pushConciergeEvent({
      source: 'concierge',
      event: 'concierge.room.created',
      roomName,
    });

    return NextResponse.json({ room: mapRoom(room) }, { status: 201, headers: noStoreHeaders() });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to create room';
    const status = message.toLowerCase().includes('already exists') ? 409 : 500;
    return NextResponse.json({ error: message }, { status, headers: noStoreHeaders() });
  }
}
