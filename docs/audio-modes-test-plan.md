# Audio modes: test and simulation plan

Status: proposal. The page's gate is in the room (`services/web/lib/room-floor.ts`); parts of the bot's side are on unmerged branches. Three modes, built test-first:

| Mode | Who may speak | Bot speaks when |
|---|---|---|
| **normal** | Anyone, open mic (today) | Today's rules, unchanged |
| **ptt** | Anyone holding the button; overlap allowed | Nobody is holding and it is quiet |
| **baton** | Only the holder | The baton is free and it is quiet |

Decisions are in the last section. Numbers marked \* are proposals.

## Design the tests assume

- **One setting:** `audio_mode` = `normal | ptt | baton`, default `normal`.
- **One pure class:** `meetlab_bot/floor.py:Floor`, with times passed in like `InterruptionTracker`.
  Normal rooms have no `Floor`.
- **Push-to-talk signal:** LiveKit's `track_muted` and `track_unmuted`, no new messages.
- **Baton state:** in the bot's memory, published as bot participant attributes. It dies with
  the bot, so if there is no bot, or no answer to a claim within 1.5 s\*, a press opens the mic and release
  closes it.
- **Enforcement:** the page gates the mic, and the bot drops non-holders' audio after the
  recorder. Server revocation is rejected because it outlives a crashed bot.
- **Bot's "press":** a check at its first audio. If someone is active it cancels, so the
  ~1.2 s of generation holds nothing.
- **Turn end:** keep `SpeechTimeoutUserTurnStopStrategy`. `ExternalUserTurnStopStrategy` doesn't
  fit: `turn_frames` sends the transcript before the stop, and its speaking flag is room-wide,
  so one person's release could commit while another holds.

## Hazards found

| Hazard | Evidence |
|---|---|
| A person starting inside the bot's first 600 ms isn't acted on until the next sentence starts | Reproduced that the onset returns False; the rest is from reading |
| `say()` (greeting, closing) and typed chat bypass any reply gate | Read |
| `track_muted` arrives 52–90 ms before the last audio, so acting on the event alone drops final words | Measured, local LiveKit, n=60. Last arrival after a mute was 121 ms |
| Latency rises about 40 ms after the first mute cycle | One comparison, local; cause not established |
| A killed participant stays visible for about 22 s, and its frames look like a mute. "Holder disconnect frees" takes that long after a crash | Measured with a killed Python client, n=8; a killed browser wasn't tested |
| Stale audio arrives about 50 ms after a killed participant's disconnect event | Seen in 11 of 11 kill and stop runs; length not measured, cause unknown |
| First press with the mic off at PreJoin may start that speaker's WAV late | Read; not run. The one remaining way a WAV could lose alignment |

**Ruled out at SDK level (2026-11-05):** a muted publisher's `AudioStream` keeps yielding 100 frames/s
of digital zero, drifting at most 10 ms over 100 s, for Python and Chromium publishers. So Pipecat's
1.0 s forced stop and collapsed WAV gaps shouldn't occur. This is inferred until checked through
`AudioTrackSink`, on LiveKit Cloud, because the measurements used local LiveKit only.

At the bot's pipeline those frames are near zero, not zero: peak 1 (real page, real Pipecat
transport, local LiveKit). Pipecat's resampler is the likely cause, not confirmed.

## Test ladder

Normal mode's existing tests (`test_interruption*`, `test_multi_speaker_stt`, `test_smart_turn`,
`test_rtvi`) stay unedited in feature PRs. Any diff there needs review.

### Normal must not change

| Test | Asserts |
|---|---|
| `test_normal_trace_is_unchanged` | Golden frame and tracker sequence from today's `main`. Green from day one |
| `test_normal_room_sends_no_floor_messages_or_events` | 0 of each |
| `test_normal_mic_policy_matches_today` | Mic follows PreJoin, control-bar toggle present, no hold button |
| `test_normal_wav_bytes_unchanged` | No padding added |
| `test_audio_mode_default_is_normal` | Column and server default are `normal` |

### Floor rules: `services/bot/tests/test_floor.py`

Written, red until `floor.py` exists: long hold, overlap, claim race, person beats bot, bot claims
free baton, non-holder release ignored, disconnect frees, new bot starts free, normal has no floor.

Still to add: `test_press_during_generation_blocks_first_audio` (0 audio frames) and
`test_walked_away_holder_loses_baton` (silent 15 s\*).

### Bot pipeline, fake frames: `services/bot/tests/test_floor_pipeline.py`

| Test | Pass criterion |
|---|---|
| `test_release_closes_transcript_keeps_last_words` | Frames stopped or silent: final word present, 1 turn, under 1.0 s |
| `test_next_press_after_forced_stop_fires_onset` | Exactly 1 onset, 0 warnings |
| `test_overlapping_holders_stay_attributed` | 2 speakers, 2 turns |
| `test_reply_waits_until_everyone_releases` | 0 TTS frames while held and heard |
| `test_speech_while_holding_yields_the_bot` | Yields once per response |
| `test_fixed_lines_and_chat_replies_obey_the_gate` | 0 audio while held |
| `test_non_holder_audio_never_reaches_stt` | 0 frames to STT; recorder receives all |
| `test_floor_events_are_logged` | Press, release, claim, grant, deny, timeout: 1 event each, with event time |
| `test_hung_event_log_does_not_delay_grant` | 0 ms waited |
| `test_wav_keeps_time_across_hold_gap` | Integration, not fake frames: real `AudioStream` to Pipecat to `AudioTrackSink`. Drift ≤50 ms\* over a 60 s hold; hold is near zero (peak 1 measured at the pipeline, so allow a small limit), speech intact |

### Web: `services/web/lib/floor.test.ts`

No jsdom here: use EventTarget fakes and `renderToStaticMarkup`.

| Test | Pass criterion |
|---|---|
| `test_ptt_mic_opens_on_hold_closes_on_release` | Pointer and Space; 1 enable per hold despite key repeat |
| `test_blur_and_pointercancel_release` | Mic closed in the same tick |
| `test_space_in_chat_input_never_keys_mic` | 0 enables |
| `test_toggle_alternative_has_aria_pressed` | Latched toggle, no hold needed |
| `test_ptt_blocks_other_mic_paths` | No control-bar or shortcut route opens the mic |
| `test_camera_untouched_by_floor_changes` | 0 camera calls in 5 cycles |
| `test_baton_press_waits_for_grant` | Mic closed until grant |
| `test_no_bot_opens_floor` | No bot, or no answer in 1.5 s\*: a press opens the mic; it never opens unpressed |

### Settings, stack and live

| Test | Where | Pass criterion |
|---|---|---|
| `audio_mode` in `BEHAVIOURAL_FIELDS` | `tests/integration/test_config_contract.py` | Fallback test and `make test-config-parity` enforce it |
| `test_put_rejects_unknown_audio_mode` | `services/api/tests/test_runner_start.py` | 400 |
| `test_connection_details_returns_room_audio_mode` | `connection-details.test.ts` | Room scope wins |
| `test_floor_events_reach_events_table` | `tests/integration/test_audio_mode.py` | Rows readable by room |
| `scenario_ptt` | `tests/system/acceptance.py` | Real press in Chromium; reply only after release; camera stays published |
| `scenario_baton_kill9` | same | Floor opens after a killed bot; limit set by the first run |
| `scenario_ptt_recording` | same | MP4 and per-speaker WAV available |

The settings change touches more than the three places in `CLAUDE.md`: also
`db/config_loader.py:EffectiveBotConfig` and `connection-details/route.ts`.

**What CI can't run:** it has no ElevenLabs key, so the bot ends before anyone speaks (#256).
Fake-frame tests stand in on each PR. Live scenarios run in the release pipeline and fail rather
than skip.

### Order

1. `test_normal_trace_is_unchanged`: the net goes in first; the one test that starts green.
2. `test_audio_mode_default_is_normal`: forces the setting.
3. `test_a_hold_past_3s_still_counts_as_speaking`: forces hold state.
4. `test_release_closes_transcript_keeps_last_words`: worst research loss, silently missing words.
5. `test_baton_claim_race_has_one_winner`: every baton test depends on it.

## Settle by running first

Local stack, stubbed LLM and TTS, no paid services:

1. **Answered:** the bot's `AudioStream` yields zeros while the publisher is muted, so no WAV padding.
2. **Answered:** `LocalAudioTrack.mute()` no longer churns tracks in livekit 1.1.7 (0 track events
   over 120 cycles), so simulated people can mute. Reply rates weren't retested.
3. **Answered locally:** `track_muted` overtakes the last audio. A 200 ms\* release tail covers the
   121 ms seen; re-measure on LiveKit Cloud before fixing the number.
4. **Not run:** press-to-audible latency, including a first press with the mic off at PreJoin.
5. **Not run:** order of `BotStopped` and assistant turn stopped, for replies and `say()`.
6. **Answered for a Python client:** about 22 s, so "no bot" can't rely on presence. The 1.5 s\*
   no-answer rule stays.

## Simulations: `meetlab_loadtest/sim_modes.py`

The same script runs in each mode, so mode is the only variable. A pure
`verdict(mode, scenario, timeline)`, shaped like `sim_attribution.py:verdict`, is unit-tested on
handwritten timelines. Live, normal passes today; ptt and baton fail until built.

### Scenarios (28 cells)

| Behaviour | People | Modes | Expected |
|---|---|---|---|
| Turn-taking | 1, 2, 3 | all | SLOs hold |
| Overlap (0.15 s offset) | 3 | all | Baton: no overlap heard, queue in order |
| Interrupt the bot | 1, 3 | all | Bot yields; baton: finishes its sentence, then yields |
| Long hold (45 s) | 2 | all | No timeout; one turn |
| Press while the bot generates | 2 | all | Bot silent while a mic is live |
| Walked away holding | 2 | ptt, baton | Released after 15 s\*, next granted; ptt: bot may speak |
| Late release (+2 s) | 1 | ptt, baton | Baton: bot waits |

Timing comes from a digest of dialogue and line, so it is the same across modes: pre-roll
250±100 ms, post-roll 300±150 ms, 10% press late, 10% release early. These are guesses with no
human data behind them, so they stay settings.

### Metrics, one row per run

| Metric | Definition |
|---|---|
| Reply time | First bot audio minus last speech frame; also from release |
| Turns answered | ≥95% |
| Split, missed, WER | Per speaker (`quality.py:hearing`) |
| Clipped first/last | First or last 2 words absent from stored text; normal is the baseline |
| Attribution | Share of lines under the right speaker; merged turns |
| Talk-over, yields | Bot audible after onset; audio stopping within 1 s |
| Bot share | Bot audible seconds over all audible seconds |
| Overlap | Time with two or more people speaking |
| Dead time | Press to grant, p50 and p95 |
| Stuck floor | Held over 20 s with nobody audible; any event fails the run |
| Judged quality | `judge.py`, opt-in separate step |

**A simulation can't measure** how a mode feels, button fatigue, or people changing how they talk.
Recorded lines don't react to the bot. **The data can eliminate a mode, not choose one**; a human
pilot chooses among the survivors.

### Comparison

- Repeats: 5 valid for 2- and 3-person turn-taking, overlap and 3-person interrupt; 3 elsewhere.
  108 valid runs, about 130 attempts.
- Paired by scenario, people and repeat, with mode order rotated and bootstrap intervals.
- Runs are invalid only on infrastructure signals (lag, `stt-cpu` restart), never outcomes,
  counted by mode. Otherwise failures a mode causes would disappear.
- 5 of 5 clean still allows a failure rate of about 45%, so report rates with intervals.
- Gates for any mode: SLOs in `docs/testing.md`, zero stuck floors. Proposed margins: judged −0.2,
  WER +1 point, reply +200 ms, clipped +2 points.

### Commands and cost

| Command | Does | Spend (estimate) | Time |
|---|---|---|---|
| `make sim-modes-check` | Unit verdicts | $0 | Seconds |
| `make sim-modes MODE= SCENARIO= PEOPLE=` | One live run | Under $0.10 | ~3 min |
| `make sim-modes-matrix COST_OK=<usd>` | Full matrix, refuses without the flag | $4–10 with OpenAI voice; $6–50 with ElevenLabs | ~6.5 h serial |
| `make sim-modes-report [JUDGE=1]` | Offline report; judging ~$2 | $0 | Minutes |

Prices are list or third-party, not checked against our bills. Participant audio is pre-recorded;
speech-to-text and LiveKit run locally. Local 3-speaker runs have been noisy below 8 GB of Docker
memory, and this machine has 7.75 GiB, so reserve memory for `stt-cpu` before the matrix.

## Decisions (2026-11-05)

1. **Push-to-talk, holder walks away:** after 15 s\* of no speech the bot may speak, matching baton's
   idle release. No limit on a hold with speech in it.
2. **Baton, press while the bot speaks:** the bot finishes its sentence, then yields.
3. **Interruption in ptt:** speech yields the bot, not the press, as in normal, so modes stay
   comparable.
4. **No bot, or no answer:** the mic works while the button is pressed and closes on release. It
   never opens unpressed.
5. **Stuck floor:** 20 s, above the 15 s idle release, so a walked-away run can pass.
6. **The 3 s hazard in normal:** fixed first, in its own PR, before any baseline run. Baselines taken
   before that fix aren't comparable.
