# Session time limits

A meeting can be given a time budget — "you get 30 minutes, then wrap up and move
on". Participants see a countdown in the corner of the call and get warnings as the
end approaches, in the style Zoom uses for its meeting limit.

The limit is **enforced**: when time is up the assistant says a closing message, and
a short while later the room closes for everyone. See
[How a session ends](#how-a-session-ends) below.

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

## How a session ends

When the limit is reached, in this order:

1. **Nobody can start or cut off the assistant any more.** What people say from here on
   no longer reaches the assistant, so it does not begin a new answer and cannot be
   interrupted.
2. **The assistant finishes the answer it is giving**, all of it (given up on after 30 s).
3. **It waits for a pause** if a participant is talking, for at most 10 s.
4. **It says the closing message** (Bot settings → Session limit → *Closing message*).
5. **The room closes for everyone** a set time after the closing message ends: Bot
   settings → *Seconds before the room closes*, 30 by default, 0 to 600.

Closing the room is what shows each participant the "Thank you for taking part" screen
with their completion code, all at the same moment.

If the closing message produces no sound within 20 s (for example the voice service is
down), the room still closes after the buffer.

Why it works this way: on 2026-11-18 a participant spoke just as the limit was reached,
which threw away the closing message before it was heard, and nothing ended the meeting.

Still true:

- The assistant never speaks about the time *before* the limit: no spoken "five
  minutes remaining" cutting across the conversation.
- Recording and transcripts stop when the room closes, as they do when everybody leaves.

## Where it lives, for engineers

| Piece | File |
|---|---|
| Config column (`bot_config.session_limit_minutes`) | `packages/core/meetlab_core/db/models.py` |
| Effective-config resolution | `packages/core/meetlab_core/db/config_loader.py` |
| `GET`/`PUT /config` validation | `services/api/meetlab_api/runner.py` |
| Console field | `services/web/app/(shell)/config/page.tsx` |
| Handed to the browser at join | `services/web/app/api/connection-details/route.ts` |
| All the logic (pure, unit-tested) | `services/web/lib/session-limit.ts` |
| Countdown pill + notifications | `services/web/lib/SessionTimer.tsx` |

The browser's countdown has no stored deadline: it derives
everything each second from three values: the configured limit, the earliest
`joinedAt` LiveKit reports for a non-bot participant, and the current time. That is
why late joiners share one countdown with no extra machinery — and also why an empty
room resets the clock.

The assistant keeps its own clock from the first join (`closing_due` in
`services/bot/meetlab_bot/study_support.py`), and the end of the session is decided in
`services/bot/meetlab_bot/session_end.py` (pure, unit-tested); `bot.py` (`_announce_close`)
acts on it and deletes the LiveKit room.

Tests, all under `make test-unit`:

- `services/web/lib/session-limit.test.ts` — phase thresholds, anchor selection, clock
  formatting, notification copy and de-duplication, plus a second-by-second walk of
  a whole session at five different limits asserting the countdown only ever counts
  down, the phase only ever escalates, and each notification fires exactly once.
- `services/web/app/api/connection-details/connection-details.test.ts` — the limit reaches
  the browser, and a bot runner that is down, erroring, or returning junk still
  yields a joinable room with no limit.
- `services/api/tests/test_runner_start.py` — validation (including `True`, which is
  an `int` in Python), the 0 and 1440 bounds, and that a room set to `0` overrides a
  global limit instead of inheriting it.
- `packages/core/tests/test_defaults.py` — the column defaults to `0`; the buffer to `30`.
- `services/bot/tests/test_session_end.py` — the order above, each wait's limit, the
  buffer counted from the real end of the closing message, and the room closed once.
- Live, after every release (`session_limit` in `tests/system/acceptance.py`): a 1-minute
  room hears its closing message and is closed with the reason the page needs to show
  the completion code.
