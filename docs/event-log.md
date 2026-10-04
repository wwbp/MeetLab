# Errors & events

Every part of the stack records what it did — and what went wrong — in one place:
the `events` table in the bot runner's database. The console reads it at
**Console → Errors & Events**, defaulting to errors only.

It survives restarts and deploys, which the previous in-memory version did not.

## What lands there

| Source | Examples | Severity |
|---|---|---|
| **meet** (admin actions) | `concierge.room.created`, `concierge.bot.started` | info |
| **meet** (route failures) | `meet.route.error` — with the route, message and stack | error |
| **LiveKit** (webhooks) | `participant_joined`, `room_finished`, `egress_ended` | info |
| **LiveKit** (lost recording) | `egress_ended` with a failed or aborted egress | **error** |
| **bot runner / bots** | any `logger.warning` or `logger.error` anywhere in the Python code | warning / error |

The last row is the important one: it is a *catch-all*. Nobody has to remember to
report an error — every warning and error the runner or a bot logs is mirrored
automatically, with the file, function, and line it came from. If the code did
`logger.bind(room_name=..., conv_id=...)` first, the event is also joinable to the
session it happened in.

Deliberately **not** mirrored: `info` and `debug` logs. A single session writes
hundreds of them and they would bury the incidents this view exists to surface.
Those still go to CloudWatch.

## Reading it

The console view filters by severity (errors / warnings / info / everything) and
by room, refreshes every 15 seconds, and expands any row to show its full payload.

Two other ways in:

- `GET /api/concierge/events?severity=error&room=<room>&limit=100` (console session)
- SQLAdmin at `/api/db` → Events, for ad-hoc querying and sorting

If the log itself is unreachable, the console says so rather than showing an empty
list — "no events" and "cannot read events" must not look the same.

## For engineers

| Piece | File |
|---|---|
| Table + severity column | `agent-runner/db/models.py` |
| `record_event()` and the log-mirroring sink | `agent-runner/event_log.py` |
| `POST /events`, `GET /events` | `agent-runner/runner.py` |
| meet's writer, reader, and error reporter | `meet/lib/concierge/event-log.ts` |
| `pushConciergeEvent` façade | `meet/lib/concierge/events-store.ts` |
| Console API | `meet/app/api/concierge/events/route.ts` |
| Console view | `meet/components/desk/events-tab.tsx` |

Two rules the code follows, both load-bearing:

1. **Writes never throw and never block.** An event describes an operation and must
   not be the thing that breaks it. meet defers writes with `after()` so nothing on
   the request path waits; the Python sink hands off to the event loop and swallows
   failures without logging (logging inside a log sink would loop).
2. **Reads throw.** A console that renders an empty list when the log is down would
   read as "nothing went wrong", which is worse than an error message.

To record something explicitly:

```python
# agent-runner
from event_log import record_event
await record_event("bot.recording.failed", severity="error",
                   room_name=room, payload={"reason": str(exc)})
```

```ts
// meet — inside a route
pushConciergeEvent({ source: 'concierge', event: 'concierge.room.created', roomName });
noteRouteError('POST /api/concierge/rooms', error, { roomName });
```

## Console auth

The console is protected by a path allow-list in `meet/middleware.ts`, which means a
new page or admin API is **public until it is added there**. `meet/middleware.test.ts`
enumerates the console shell from the filesystem and asserts every page and admin
route is covered, so that mistake fails a test instead of shipping.

`/api/record/*` sits outside that list but checks for itself: only a logged-in console
session may start or stop a recording (`lib/record-auth.ts`, F10, 2026-10-04). Participants
have no Record button any more: anyone who names a room can join it, so "in the room" proves
nothing, and a study's recording must not be stopped from inside it. Studies record with
Bot Config's auto-record.
