# Session time limits

A meeting can be given a time budget — "you get 30 minutes, then wrap up and move
on". Participants see a countdown in the corner of the call and get warnings as the
end approaches, in the style Zoom uses for its meeting limit.

The limit is **advisory**: nobody is disconnected. At zero the countdown says the
time is up and offers a Leave button, but the call keeps working. See
[Enforcement](#enforcement-what-this-does-not-do) below.

## Setting it

Console → **Bot Config** → **Session Limit** → *Minutes*.

- `0` means **unlimited**, and is the default. Existing rooms are unaffected until
  someone sets a limit.
- Valid range is `0`–`1440` (24 hours).
- Like every other field on that page, it is set **per scope**: `global` is the
  fallback, and a row named after a specific room (or a preset name used by a start
  link) overrides it for those meetings only.
- Start links copy the whole config of the scope they picked onto the new room, so a
  limit set on a preset applies to every meeting started from links using it.

## What a participant sees

Say the limit is 30 minutes.

| When | Where | What |
|---|---|---|
| On joining | notification | "This session is limited to 30 minutes" |
| Throughout | pill, top-left of the call | `24:13` counting down |
| At 22:30 elapsed (¾ of the way) | notification + pill turns amber | "About 8 minutes left in this session" |
| At 29:00 elapsed (1 minute left) | notification + pill turns red | "Less than 1 minute left — time to wrap up" |
| At 30:00 | pill reads `0:00 · time's up` | A notification that stays on screen, with a **Leave** button |

Short sessions move the final reminder earlier so it doesn't land on top of the
three-quarter warning: it fires one minute out, or at ⅛ of the limit for anything
under eight minutes (a 4-minute limit warns at 3:00 and again at 3:30).

## When the clock starts

**When the first person joins.** Not when the room is created, and not when the
assistant starts up — the assistant is in the room before anyone else, and counting
from there would silently eat part of the budget.

Everyone in the room shares that one clock, so somebody who joins 20 minutes into a
30-minute session sees 10 minutes left, not 30.

Two consequences worth knowing:

- **If everyone leaves and someone rejoins, the clock restarts.** The start of the
  session is taken to be the earliest join time among the people currently in the
  room, so an empty room resets it. Practically: a single participant who reloads
  their browser tab gets a fresh budget.
- **If the assistant is unreachable when you join**, the call still connects — it
  just has no limit that time. Joining a meeting never depends on the limit lookup
  succeeding.

## Enforcement: what this does *not* do

Nothing forces anyone out. The countdown and warnings are the whole feature: it
tells people their time is up and trusts them to leave.

Two other things it deliberately does not do:

- The assistant never *speaks* about the time — no spoken "five minutes remaining"
  cutting across the conversation.
- Recording, transcripts, and the assistant itself are unaffected. They stop the way
  they always have: when everybody has left the room.

## Where it lives, for engineers

| Piece | File |
|---|---|
| Config column (`bot_config.session_limit_minutes`) | `agent-runner/db/models.py` |
| Effective-config resolution | `agent-runner/db/config_loader.py` |
| `GET`/`PUT /config` validation | `agent-runner/runner.py` |
| Console field | `meet/app/(shell)/config/page.tsx` |
| Handed to the browser at join | `meet/app/api/connection-details/route.ts` |
| All the logic (pure, unit-tested) | `meet/lib/session-limit.ts` |
| Countdown pill + notifications | `meet/lib/SessionTimer.tsx` |

There is no stored deadline anywhere and no timer on the server. The browser derives
everything each second from three values: the configured limit, the earliest
`joinedAt` LiveKit reports for a non-bot participant, and the current time. That is
why late joiners share one countdown with no extra machinery — and also why an empty
room resets the clock.

Adding real enforcement later would mean one scheduled `remove_participant` call in
`agent-runner/bot.py` at the deadline; nothing in the current design would change.

Tests, all under `make test-unit`:

- `meet/lib/session-limit.test.ts` — phase thresholds, anchor selection, clock
  formatting, notification copy and de-duplication, plus a second-by-second walk of
  a whole session at five different limits asserting the countdown only ever counts
  down, the phase only ever escalates, and each notification fires exactly once.
- `meet/app/api/connection-details/connection-details.test.ts` — the limit reaches
  the browser, and a bot runner that is down, erroring, or returning junk still
  yields a joinable room with no limit.
- `agent-runner/tests/test_runner_start.py` — validation (including `True`, which is
  an `int` in Python), the 0 and 1440 bounds, and that a room set to `0` overrides a
  global limit instead of inheriting it.
- `agent-runner/tests/test_defaults.py` — the column defaults to `0`.
