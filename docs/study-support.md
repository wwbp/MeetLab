# Running a paid study

!!! warning "A participant must speak to get a completion code (since 2026-10-27)"
    The completion code is shown only to a participant the bot has **heard speak** in
    the room. Someone who joins but never says anything gets **no code**; their screen
    tells them to contact the researcher. Tell participants up front, in the study
    description: *"Talk with the assistant; your completion code appears when you leave."*
    Why: before this, anyone could fetch a valid code without attending (§2).

Two things a study needs that a demo does not: every session has to be
attributable to a participant who can be paid, and every participant has to
leave with proof they sat the session. Nobody is blocked from the room and
nobody is disconnected, because the pilot showed that a participant stuck on a
form is a participant who emails you instead. The one gate is the code itself:
it needs the participant to have spoken.

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

To pull the IDs for a finished study, open the console's **Meetings** page and press
**People** under a meeting: it lists everyone who spoke with their Prolific ID, and
**Download CSV** saves them as a spreadsheet (room, meeting start, display name,
prolific_id, first spoke at, prolific_id_invalid). The same data as JSON:
`GET /api/meetings/<conversation id>/speakers` (logged in) lists everyone who spoke, in the
order they first spoke, with `prolific_id` (or `prolific_id_invalid`). In SQL it is a join
of `utterances` → `speakers` reading `meta->>'prolific_id'`.

The whole flow is checked live after every deploy: a participant joins with a Prolific ID
before the bot, they get no completion code until the bot has heard them, then it must equal
HMAC(room:ID), and their ID must be stored (`study_prolific`); and a room limited to one minute must hear its closing message
(`session_limit`). `tests/system/acceptance.py`.

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

The screen asks for the code when it opens (`/api/completion-code`), and meet
issues it only to a Prolific ID the bot heard speak in one of that room's
sessions. Until 2026-10-27 it came with the join, so anyone could fetch a code
for any Prolific ID without attending. A participant who never spoke sees
"We could not confirm you took part" and a **Try again** button instead, and
should contact the researcher.

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
- nobody can produce one without the secret, and meet only shows it to someone
  who spoke in the room;
- you can recompute anyone's code from your Prolific export to check it.

There is deliberately **no completion gate** on leaving. The session ends when the timer
runs out, and anyone may leave at any time; the only check is the one above, that the
code goes to someone who spoke. Participants who left early are filtered out of the
data afterwards, which is both easier and more honest than blocking the exit.

### Is the code unique?

Per participant per room, yes: it is 8 characters from a 32-letter alphabet (about 1.1
trillion codes), so two participants practically never share one. It is also *repeatable*:
the same person in the same room always gets the same code, even after reconnecting, which
is what lets you recompute and check it. There is no code without a Prolific ID: the pre-join
screen requires one.

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

## 4. Group start links (since 2026-11-02)

A start link sends every participant who opens it to a meeting with a bot. With a
**room size** above 1, participants first wait on the link's page until enough of them
have arrived; then they meet together, with one bot, in a new room. Participants reach the
link from Prolific through your Qualtrics survey, and their **Prolific ID is their
participant ID** throughout: in the line, in the room and on the completion code.

A link of room size 1 with start delay 0 (the defaults) behaves as start links always
have: each person who opens it gets a meeting of their own, at once.

### Creating a link

Console → **Start links**. On the left are your saved bot settings (search, click to add);
on the right, the ones this link uses. Then fill in:

| Field | Default | What it does |
|---|---|---|
| **Name** | (required) | For you only, in the list of links, e.g. "Pilot 3, groups of 3". |
| **Room size (people)** | 1 | How many participants meet in one room, 1 to 8. |
| **Min size** | the room size | Shown when the size is above 1. If the line has waited for **max wait** without filling a room, the people waiting start with at least this many. Equal to the room size means never start short. |
| **Start delay (seconds)** | 0 | A countdown, 0 to 300 seconds, between the group being confirmed and everyone entering the room. |
| **Max wait (minutes)** | 5 | How long a participant waits for a group, 1 to 30 minutes, counted from when they first joined the line. After that they are told no group could be formed. |
| **Time to confirm (seconds)** | 60 | Shown when the size is above 1. When a group forms, each person must press **Ready** within this time, 10 to 300 seconds. 60 leaves room for someone whose tab is in the background. |
| **Password** | none | Optional. Put it on the survey page, next to the link: you **cannot see it again** after you create the link. |

**Create link** gives you the address, `https://<host>/start/<link id>`. In Qualtrics, link
to it with the Prolific ID on the end:

```
https://<host>/start/<link id>?PROLIFIC_PID=${e://Field/PROLIFIC_PID}
```

(`${e://Field/PROLIFIC_PID}` is Qualtrics's embedded-data field, filled from the
`PROLIFIC_PID` that Prolific puts on the survey's address.) With a room size above 1 the
ID is checked at the ready check, above the **I'm ready** button: prefilled from the link,
correctable, and required. Keep it on the link: it also keeps one person to one place in the
line from the moment they join, and records people who wait and never reach a group. With size 1 it is asked on the pre-join
screen, as before (§1), and the ID travels on to the room either way.

**Which bot setting each room gets.** Each new room takes the setting used least so far on
this link; if several are tied, one of them at random. So with two settings, rooms alternate
between them and stay balanced however many arrive. A group that falls apart at the ready
check (below) does not count. A link only accepts saved bot settings: a name that doesn't
exist is refused when you create it.

### The list of links, stopping and starting

Under the form, every link with its settings and live counts:

- **Waiting now**: in the line or at the ready check.
- **In rooms**: entered a room (now or earlier).
- **Expired**: waited the full max wait without a group.
- **Missed**: did not press Ready in time.

**Stop** closes a link to new participants: anyone who opens it reads "This link is
closed… Go back to the survey and contact the researcher." People already waiting, and
meetings already running, carry on. **Start** opens it again. Stop a link at the end of a
study so late arrivals don't wait for a group that will never form.

If a bot setting a link uses is **deleted** (in Bot settings or the Database page), the link
**stops by itself** within a few seconds, and the Events page shows a `link_stopped` warning
naming the missing setting. Otherwise its rooms would quietly run on the global settings.
Save the setting again under the same name, then press **Start**. Until you do, **Start**
is refused and names the missing setting.

The password is checked on every join. After too many wrong tries the page asks the
participant to wait 1 minute.

### What participants see

1. **Join the group meeting.** The password if the link has one, and **Join**. The page says to keep it open and in front.
2. **Finding your group: 2 of 3 here.** The count updates every 2 seconds, with the time
   they will be looked for until (their max wait). Closing the page gives up their place:
   someone who hasn't been seen for 90 seconds is no longer matched. Reopening the link
   with the same Prolific ID on it, while their place is still held, returns them to it.
3. **Your group is ready.** The tab's title changes to "(!) Your group is ready", a tone
   plays, and the **Prolific ID** field (prefilled from the link) appears above the **I'm
   ready** button, with the seconds left to press it (Time to confirm). The button needs a
   valid ID, and refuses one that someone else in this line holds. After pressing: "Waiting for the others to confirm."
4. **Starting in** with a countdown (the start delay), after "Setting up the room…" while
   the bot is started. Next comes the room itself: "allow your microphone and type your
   name." With a delay of 0 they go straight in.
5. In the room everything is as in a 1-on-1 meeting (§1, §2): the closing message, and the
   completion code on leaving.

When something goes wrong:

- **Someone doesn't press Ready in time.** That person sees **You missed the start** and a
  **Join again** button, which puts them at the **back** of the line, with a new max wait.
  Everyone who did press Ready goes back to the line **with their place kept** and reads
  "Someone did not confirm in time. You are back in line with your place kept." Their max
  wait still counts from when they first joined.
- **No group within max wait.** They see **We could not form a group in time**: "Go back to
  the survey and follow its instructions." Write those instructions into the survey.

### Who gets a completion code

The rule of §2 is unchanged: a code goes only to someone the bot **heard speak** in the room.

| What happened | Completion code |
|---|---|
| Entered the room and spoke | Yes, on leaving |
| Entered the room and never spoke | No; told to contact the researcher |
| Waited until max wait, no group | No: there is **no waiting code**. They never entered a room |
| Missed the ready check and left | No, unless they press Join again and later meet |

Decide before the study how you will treat people who waited and got no group (for
example a fixed payment or a bonus in Prolific), and say so in the survey. To list them,
Database → **Tickets**, searched by the link's ID, those with state `expired`; or in SQL:

```sql
SELECT t.participant_id, t.queued_at, l.name AS link
FROM tickets t JOIN start_links l ON l.link_id = t.link_id
WHERE t.state = 'expired' AND l.name = 'Pilot 3, groups of 3'
ORDER BY t.queued_at;
```

### Who was in which room, with which setting

Without SQL: the console's **Database** page. **Tickets**, searched by the link's ID (the
part after `/start/` in its address, or the participant's Prolific ID), lists each person
with their `state` and `room_name`; **Matches**, searched by the same link ID, gives each
room's `bot_config_scope` (its bot setting). Both download as CSV.

In SQL it is one query: each participant's place in the line (`tickets`), their group
(`matches`), the room's meeting (`conversations`) and its bot setting (`bot_config`). The
Database page browses tables but does not run SQL, so this is for anyone with database
access (an engineer can run it on production); change the link's name:

```sql
SELECT t.participant_id    AS prolific_id,
       m.room_name,
       m.bot_config_scope  AS bot_setting,
       m.size              AS people_in_group,
       c.id                AS conversation_id,
       c.started_at, c.ended_at,
       b.llm_model, b.tts_voice
FROM tickets t
JOIN start_links l        ON l.link_id = t.link_id
JOIN matches m            ON m.room_name = t.room_name
LEFT JOIN conversations c ON c.room_name = m.room_name
LEFT JOIN bot_config b    ON b.scope = m.bot_config_scope
WHERE l.name = 'Pilot 3, groups of 3'
  AND t.state IN ('placed', 'done')
ORDER BY m.created_at, t.participant_id;
```

- `prolific_id` is filled for links of size above 1 once the person presses Ready, or from
  joining if the link carried it. Someone who joined from a link without the ID and never
  pressed Ready has none. A size-1 link asks for the ID only on
  the pre-join screen, so its rows here have none: the ID is with the speaker (§1).
- Only `placed` (in the room now) and `done` (meeting over) people entered a room. A row in
  any other state is someone still waiting, who missed the ready check (`removed`) or who
  expired.
- A room has more than one conversation if its bot was replaced mid-meeting (it rejoins
  with context); each appears as its own row.
- `bot_config` holds the setting **as it is now**. If you edit a setting during a study,
  the rooms before the edit ran with the old values; `bot_setting` still names it.
- The bot setting's other columns (prompt, models, voice) can be added to the `SELECT`
  as `b.<column>`. Who actually spoke, and their completion codes, are in §1 and §2:
  **Meetings → People** for each `conversation_id`.

### Warnings on the Events page

The Events page shows errors only at first: choose **warnings** to see these.

| Event | What it means | What to do |
|---|---|---|
| `ready_failed` | A group formed, but someone didn't press Ready in time. They were taken out of the line; the others went back with their place kept. | One now and then is normal. Many mean participants leave the page in the background: tell them in the survey to keep it in front, or raise **Time to confirm**. |
| `ticket_expired` | Someone waited the full max wait with no group. | Too few people arriving at once for the room size. Recruit in bursts, lower **Min size**, or raise **Max wait**. Each is someone to handle by your no-group rule above. |
| `link_stopped` | A link stopped itself because a bot setting it uses was deleted. | Save the setting again under the same name, then **Start** the link. |

`match_formed` (a group formed, with its room and bot setting) is information, shown under
**info**. Payloads: `docs/event-log.md`.

## Verifying a code

```bash
node -e "console.log(require('./meet/lib/completion-code').completionCode('<room>', '<prolific id>', process.env.LIVEKIT_API_SECRET))"
```
