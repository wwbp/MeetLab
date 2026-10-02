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

A malformed ID that somehow gets through (a stale or bypassed client) is stored
under `prolific_id_invalid` rather than dropped. An unmatched session is a
participant who did the work and cannot be paid, so it is worth keeping what
they typed.

To pull the IDs for a finished study, join `utterances` → `speakers` and read
`meta->>'prolific_id'`.

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

## Verifying a code

```bash
node -e "console.log(require('./meet/lib/completion-code').completionCode('<room>', '<prolific id>', process.env.LIVEKIT_API_SECRET))"
```
