# Use cases — what this system is for, and what proves it works

Derived from **30 days of production traffic**, not from what the code implies.
Everything below has either measured usage or measured absence. Companion to
`docs/distillation-audit.md`; this is the "what do we keep" half.

The product in one line: **a researcher runs AI-bot-mediated group meetings and
gets back transcripts and recordings.** Everything that does not serve that is a
cut candidate.

---

## Core use cases

| # | Use case | Prod evidence (30d) | Covered by |
|---|----------|---------------------|------------|
| UC1 | Researcher signs in to the console | `/login` 2,653 hits | `middleware.test.ts` |
| UC2 | Researcher configures the bot — prompt, greeting, voice, model, endpointing | `/config` 218 hits, `PUT /config` 61 | `test_runner_start` (round-trip), `test_config_contract` (knobs are live) |
| UC3 | Researcher creates a start link over a bot pool | `/start-links` 184 hits | `lib/start-link.test.ts` |
| UC4 | Participant opens a start link and is routed to a room | `/start/<token>` 8 distinct | `lib/start-link.test.ts` (token verify) |
| UC5 | Participants join and hold a conversation with the bot | `/rooms/<name>` 39 distinct rooms | `test_multi_speaker_e2e` (10 tests) |
| UC6 | The session is captured — per-speaker audio, composite video, transcript | 62 audio tracks, 13 recordings | `test_audio_tracks`, `test_recording_autostart`, `test_transcript` |
| UC7 | Researcher reviews and downloads sessions | `/meetings` 190 hits, ~16 downloads | `app/api/meetings/meetings.test.ts` |
| UC8 | Researcher monitors live rooms | `/` 6,701 hits (console home) | `lib/concierge/stores.test.ts` |

### Supporting behaviour — invisible but load-bearing

| # | Behaviour | Covered by |
|---|-----------|------------|
| S1 | Exactly one bot per room; claim released when it leaves | `stores.test.ts`, `livekit-admin.test.ts` |
| S2 | Session ends cleanly; conversation closed, media finalised | `test_session_lifecycle` (4 tests) |
| S3 | Pending recordings reconciled when the egress webhook doesn't fire | `test_recordings_transcript` |
| S4 | Advisory session time limit counts down in the browser | `lib/session-limit.test.ts` (43 tests) |
| S5 | Console surface stays behind auth | `middleware.test.ts` |

### Where the coverage is actually thin

Not "untested" — 286 + 199 tests exist. These are the gaps that matter:

1. **UC5 turn-taking is untested for correctness, only for attribution.** The ten
   e2e tests prove *who* said what and that rows land in the DB. Not one asserts
   the bot replies *in reasonable time* or *waits for the participant to finish* —
   which is exactly what failed in the pilot and why the suite stayed green
   throughout. `test_pilot_regression_dataset` now pins the numbers, but nothing
   exercises the live path.
2. **UC6 video is only tested through its failure mode.** Tests assert WAV capture
   survives egress failure — good, and it is why we still have pilot audio. Nothing
   asserts a composite recording is ever successfully produced. Half of real
   sessions had no video and no test noticed.
3. **UC3→UC4→UC5 is never tested as one chain.** Each link has unit tests; the
   journey a participant actually takes has none.
4. **`metrics.py` has no tests.** Metric names are a contract — `grafana-prom.sh`
   and every dashboard break silently on a rename.

---

## Cut list

Nothing here has been deleted. Evidence for each.

### ✅ Deleted 2026-08-05

| Thing | Evidence | Proof core survived |
|-------|----------|---------------------|
| `app/custom/` (page + `VideoConferenceClientImpl`, 132 lines) | **2 hits/30d**, zero inbound references, orphaned from the upstream LiveKit template. Took `liveKitUrl` and `token` straight from the query string and connected the browser to whatever it was given. | `pnpm build` clean, `/custom` gone from the route table, every other route intact. `SettingsMenu` is shared with `/rooms/[roomName]` and was left in place. |
| `POST /conversations/reconcile` | **0 hits/30d**, no caller anywhere in the repo. It was only a *manual trigger* — the same sweep runs on a 120s internal timer, and the tests call `reconcile_stale_conversations()` directly. | Endpoint returns 404; startup log still shows `conversation reconcile loop started (every 120s)`; `test_session_lifecycle` green. The capability is untouched — 6 prod conversations have been closed by it. |

### ⛔️ Withdrawn from the cut list — these were wrong

Checking what things are actually wired to, before deleting them, changed two
entries. Both would have broken something.

| Thing | Why it stays |
|-------|--------------|
| `POST /recordings/stop` | **Live feature, not dead surface.** `NEXT_PUBLIC_LK_RECORD_ENDPOINT=/api/record` is set in production, so `SettingsMenu` renders a record button in every participant's room (`app/rooms/[roomName]/PageClientImpl.tsx`). The button calls `/api/record/stop`, which proxies to this endpoint. Zero traffic means nobody has clicked *stop* — not that the path is dead. Deleting it would have broken an in-call control. This is a **fix-auth** item, not a cut. |
| `agent-runner/soak-results-*.json` | Not committed at all — `.gitignore:17` already excludes `soak-results-*.json`. They are local scratch files. There was never anything in the repo to remove. |

### Delete — decided in the distillation audit (D1)

| Thing | Note |
|-------|------|
| `gpt-*`, Deepgram and whisper STT paths | Parakeet only, everywhere. **Sequence matters:** stand up the dev/staging NIM *first*, or the dev loop breaks — `STT_MODEL_OVERRIDE=whisper-base` is what makes local development work today. |
| `bot_config.stt_vad_mode`, `bot_config.stt_delay` | Only read on the `gpt-*` branch; they die with it |
| `bot_config.vad_stop_secs` | Dead everywhere — column, API validation, console slider |

### Keep, despite looking dead

| Thing | Why |
|-------|-----|
| `PATCH /media-files/{file_id}` | 0 hits, but only because the egress webhook has never fired. Delete *after* fixing the webhook, or we remove the fix's landing point. |
| `app/desk/page.tsx` | 0 hits, but it is a 4-line `permanentRedirect('/')` protecting old bookmarks. Cheaper to keep than to break someone's link. |
| `components/desk/` | Legacy folder name, but `ConciergeConsole` and `MeetingsTab` are rendered by the live shell pages. Rename later, do not delete. |
| `mock_services.py` | Soak-testing only, but that is how we load-test without paying for TTS. Needed for the 75-concurrent test in the scale plan. |

### Fix, not delete

| Thing | Note |
|-------|------|
| `/api/record/start`, `/api/record/stop` | **Done (2026-10-04):** console sessions only (`lib/record-auth.ts`); the participant Record button is gone, studies use auto-record. Joining a room by its name alone is the remaining half of F10 (signed join links, in the plan). |
| `pnpm lint` | Broken since the Next 16 upgrade (`next lint` removed). No linting has run in CI for some time. |

---

## Proposed order of work

1. **RC1 + RC2** — two config values, most of the user-visible pain. UC5 becomes
   correct.
2. **Write the missing UC5/UC6 tests against the fixed behaviour** — a live
   turn-taking timing test, and one that asserts a composite recording is actually
   produced. Do this *after* the fix so the tests encode the intended behaviour
   rather than the broken one.
3. **RC3** (interruption) and **RC4** (egress retry + visible failure).
4. **Cut** the list above, in the order given — the STT paths last, after the dev
   NIM exists.
5. ~~**`/api/record/*` auth**~~ (done: console-only), then delete `PATCH /media-files` once the webhook works.

Deliberately not first: the deletions. Cutting dead code is satisfying and low
risk, but it fixes nothing a user noticed. The pilot's complaints all trace to
items 1 and 3.
