# Running a paid study

Two things a study needs that a demo does not: every session has to be
attributable to a participant who can be paid, and every participant has to
leave with proof they sat the session. Neither is a gate — nobody is blocked
from the room and nobody is disconnected — because the pilot showed that a
participant stuck on a form is a participant who emails you instead.

## 0. Prepare for study (before the first participant)

A bot needs a machine to run on. To save money, staging keeps none running when it
is idle, so the first bot of a study waits about **2–3 minutes** for a machine to
start, and the participant sits in a room without a bot. So before every study, open
the console's home page and use **Prepare for study**:

1. **Sessions at once**: how many rooms will run at the same time at the busiest point.
2. **Until**: when the study ends (at most 24 hours ahead).
3. Press **Prepare**. The line above the form shows how many machines are ready
   ("1 of 2 machines ready…"); wait until they all are, which takes about 2 minutes.

At the end time the machines are released by themselves; **Stop preparing** releases
them early. Machines already running a session are never taken away mid-meeting.
If the console says the environment "cannot hold that many sessions", it prepared as
many as it can; ask an engineer before the study (staging holds 6).

## 1. The Prolific ID

Send participants to the room with their ID in the URL:

```
https://<host>/rooms/<room>?PROLIFIC_PID={{%PROLIFIC_PID%}}
```

Prolific substitutes the placeholder. The pre-join screen prefills the field
from that parameter, and the participant can edit it — the parameter goes
missing often enough (bookmarks, refreshes, extensions that strip query
strings) that a purely derived field would strand people. The field is
**required**: the Join button stays disabled until it holds 24 letters and
numbers, which catches a typo while the participant can still fix it rather
than weeks later at payment time.

From there the ID travels as LiveKit participant metadata on the join token,
and the bot writes it onto the speaker row:

```
speakers.meta = {"role": "participant", "display_name": "...", "prolific_id": "5f2a..."}
```

The bot writes this for everyone, whether they joined before it or after. Until
2026-10-04 it didn't for people already in the room when the bot arrived (the usual
order in a study): they were learned only after their first sentence, and that path
stored their name but **not their Prolific ID**. Sessions from before then may lack it
for early joiners; their completion codes and the room's start time still match them.

A malformed ID that somehow gets through (a stale or bypassed client) is stored
under `prolific_id_invalid` rather than dropped. An unmatched session is a
participant who did the work and cannot be paid, so it is worth keeping what
they typed.

To pull the IDs for a finished study, ask the console for each session's people:
`GET /api/meetings/<conversation id>/speakers` (logged in) lists everyone who spoke, in the
order they first spoke, with `prolific_id` (or `prolific_id_invalid`). In SQL it is a join
of `utterances` → `speakers` reading `meta->>'prolific_id'`.

The whole flow is checked live after every deploy: a participant joins with a Prolific ID
before the bot, their completion code must equal HMAC(room:ID) and their ID must be stored
(`study_prolific`); and a room limited to one minute must hear its closing message
(`session_limit`). `agent-runner/tests/acceptance_staging.py`.

## 2. The closing message and the completion code

`bot_config.session_limit_minutes` has capped sessions since July, but only the
browser ever read it: the countdown hit zero and the bot carried on as though
nothing had happened. It now also reaches the bot.

When the limit elapses (measured from the first human joining, not from room
creation), the bot waits for a gap in the conversation — up to 20 seconds, so a
participant who never stops talking still hears it — and then says
`bot_config.closing_message`:

> That's all the time we have for today. Thank you so much for talking with me.
> Please leave the room now, and copy the completion code shown on your screen
> into the survey.

Edit that text per study in the console (Bot Config → `closing_message`), or
globally. A limit of `0` means unlimited, and nothing is ever said.

When the participant leaves, the room is replaced by a screen showing their
**completion code** — eight characters, with a copy button. That is the string
they paste into the survey.

That screen only appears if they press **Leave**. Closing the tab or window
mid-call would skip it, so while they are in the call the browser asks
"Leave site?" first. The wording is the browser's own and cannot be changed;
pressing Leave in the call switches the prompt off.

The code is derived, not stored: `HMAC-SHA256(LIVEKIT_API_SECRET, "<room>:<prolific id>")`,
rendered in an alphabet with no `O`/`0` or `I`/`1` because those are the
characters people get wrong when retyping. So:

- there is no table to keep in sync and nothing to lose when meet restarts
  (all of its state is in-memory);
- a code is specific to one participant in one room, so it cannot be passed
  around or reused from an earlier session;
- nobody can produce one without the secret;
- you can recompute anyone's code from your Prolific export to check it.

There is deliberately **no completion gate**. The session ends when the timer
runs out; participants who left early or never spoke are filtered out of the
data afterwards, which is both easier and more honest than blocking the exit.

### Is the code unique?

Per participant per room, yes: it is 8 characters from a 32-letter alphabet (about 1.1
trillion codes), so two participants practically never share one. It is also *repeatable*:
the same person in the same room always gets the same code, even after reconnecting, which
is what lets you recompute and check it. One exception: someone who joins **without** a
Prolific ID gets a code from their display name, so two people both called "Ana" in one room,
neither with an ID, would share a code. Studies require the ID on the pre-join screen.

## 3. Typed chat (how it behaves today)

Participants can type in the meeting's chat panel as well as speak. Today:

- **The bot receives every chat message**, including ones people send each other, and
  treats it as that person's turn: it stops what it is saying and **answers aloud**.
- **It's stored with the spoken turns**, in the same conversation, under the person who
  typed it, and marked as typed: `source` is `chat` for a typed turn and `speech` for a
  spoken one (from 2026-10-05; earlier turns have no mark). There is no separate chat log;
  the stored turns are the record.

**Open question for the team:** should the bot answer every chat message, only ones
addressed to it, or none (store them only)? Nothing changes until that's decided.

## Verifying a code

```bash
node -e "console.log(require('./meet/lib/completion-code').completionCode('<room>', '<prolific id>', process.env.LIVEKIT_API_SECRET))"
```
