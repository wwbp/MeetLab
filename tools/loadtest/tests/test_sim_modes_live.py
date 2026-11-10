"""The audio-mode simulations' driver (sim_modes_live.py) on a pretend room, in virtual time: what it
does with the mic and the floor, and that every scenario produces what its verdict looks for."""
import array
import asyncio
import contextlib
import http.server
import io
import threading
import unittest

from meetlab_loadtest.sim_modes import IDLE_S, LONG_HOLD_S, cells, hold_timing, run_list, verdict
from meetlab_loadtest.conversation_script import CONVERSATIONS
from meetlab_loadtest.sim_modes_live import (MATRIX_ATTEMPTS, PLAYS, Run, _one, _print_result, attempt_usd, budget, clip_pcm, line_for,
                                             refuse_unless_local, rerun_until_valid, stt_down)

GENERATING_S, SENTENCE_S, REPLY_S = 1.2, 2.0, 5.0


class VirtualLoop(asyncio.SelectorEventLoop):
    """Sleeps take no time. Nothing left to wait for is a hang: an error, not 8 hours."""

    def __init__(self):
        super().__init__()
        self.t, real = 1000.0, self._selector.select

        def select(timeout=None):
            if timeout is None:
                raise RuntimeError("deadlock: nothing left to wait for")
            self.t += timeout
            return real(0)
        self._selector.select = select

    def time(self):
        return self.t


class PretendRoom:
    """A room whose bot follows the plan: greets, answers when it may, waits for holds, and in baton
    keeps a queue and finishes its sentence. Every word said is stored whole under its speaker."""

    def __init__(self, mode, *, grants=True, bot_replies=True, greets=True):
        self.mode, self.grants, self.bot_replies, self.greets = mode, grants, bot_replies, greets
        self.calls = []  # (time, what, who, ...)
        self.stored, self.sentence_ends = [], []
        self.open = set()
        self.queue, self.holder, self.heard = [], None, {}
        self.reply_due = None
        self.speaking_until, self.talking = 0, 0
        self.run = None

    def start(self, run, loop):
        self.run, self.loop = run, loop
        self.bot_task = loop.create_task(self._bot())

    def now(self):
        return self.loop.time()

    def mic(self, who, on):
        self.calls.append((self.now(), "mic", who, on))
        (self.open.add if on else self.open.discard)(who)
        if self.mode == "ptt":
            self.heard[who] = self.now()

    async def floor(self, who, kind):
        self.calls.append((self.now(), kind, who))
        if kind == "claim":
            self.heard[who] = self.now()
            self.queue.append(who)
        elif self.holder == who:
            self.holder = None
            self.run.floor_changed("")
        elif who in self.queue:
            self.queue.remove(who)

    async def speak(self, who, line, skip_s, cut_s):
        self.calls.append((self.now(), "speak", who, skip_s, cut_s))
        length = max(.5, 2.5 + len(line["text"]) / 60 if len(line["parts"]) == 1 else LONG_HOLD_S + 2)
        self.stored.append((self.now() + .2, who, line["text"]))
        self.talking += 1
        end = self.now() + length - skip_s - cut_s
        while self.now() < end:
            self.heard[who] = self.now()
            if self.now() < self.speaking_until and self.mode != "baton":  # speech yields the bot
                self.speaking_until = min(self.speaking_until, self.now() + .5)
            await asyncio.sleep(.05)
        self.heard[who] = self.now()
        self.talking -= 1
        self.reply_due = self.now() + GENERATING_S

    def _active(self):
        idle = [w for w in self.open if self.now() - self.heard.get(w, 0) < IDLE_S]
        return bool(idle) or self.talking

    async def _bot(self):
        if self.greets:
            for _ in range(40):
                self.run.bot_audio()
                await asyncio.sleep(.05)
        while True:
            await asyncio.sleep(.05)
            now = self.now()
            if self.mode == "baton":
                if self.holder and self.heard.get(self.holder, 0) + IDLE_S <= now:
                    self.holder = None
                    self.run.floor_changed("")
                if not self.holder and self.queue and now >= self.speaking_until and self.grants:
                    self.holder = self.queue.pop(0)
                    self.heard[self.holder] = now
                    self.run.floor_changed(self.holder)
                    self.open.add(self.holder)
                if self.queue and now < self.speaking_until:  # finishes the sentence it is in
                    self.speaking_until = min(self.speaking_until, min(t for t in self.sentence_ends if t >= now))
            if now < self.speaking_until:
                self.run.bot_audio()
            elif self.reply_due and now >= self.reply_due and self.bot_replies and not self.holder \
                    and not self.queue and not self._active():
                self.reply_due = None
                self.speaking_until = now + REPLY_S
                self.sentence_ends += [now + SENTENCE_S, now + 2 * SENTENCE_S, now + REPLY_S]


def play(mode, scenario, people, repeat=0, **room_kw):
    """(timeline, the pretend room): the driver's whole run, greeting to the end."""
    loop = VirtualLoop()
    asyncio.set_event_loop(loop)
    room = PretendRoom(mode, **room_kw)
    run = Run(mode, scenario, people, repeat, room, loop.time)
    room.start(run, loop)
    try:
        async def whole():
            await run.after_greeting()
            await PLAYS[scenario](run)
            await run.tail()
        loop.run_until_complete(asyncio.wait_for(whole(), 400))
    finally:
        room.bot_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            loop.run_until_complete(room.bot_task)
        loop.close()
        asyncio.set_event_loop(None)
    return run.timeline(room.stored, room.sentence_ends, []), room


class EveryCell(unittest.TestCase):
    def test_every_cell_produces_what_its_verdict_looks_for(self):
        self.assertEqual(len(cells()), 28)
        for scenario, people, mode in cells():
            with self.subTest(scenario=scenario, people=people, mode=mode):
                tl, _ = play(mode, scenario, people)
                self.assertEqual(verdict(mode, scenario, tl), {"status": "pass", "problems": []})

    def test_every_repeat_of_a_scenario_works_too(self):
        for run in run_list():
            if run["repeat"]:
                with self.subTest(**run):
                    tl, _ = play(run["mode"], run["scenario"], run["people"], run["repeat"])
                    self.assertEqual(verdict(run["mode"], run["scenario"], tl)["status"], "pass")

    def test_a_bot_that_never_answers_fails_rather_than_hangs(self):
        tl, _ = play("ptt", "turn_taking", 2, bot_replies=False)
        v = verdict("ptt", "turn_taking", tl)
        self.assertEqual(v["status"], "fail")
        self.assertIn("0% of turns answered", v["problems"])

    def test_a_bot_that_never_greets_is_loud(self):
        with self.assertRaisesRegex(RuntimeError, "greeting"):
            play("normal", "turn_taking", 1, greets=False)


class SameScript(unittest.TestCase):
    def test_mode_is_the_only_variable(self):
        said = {m: sorted((x["who"], x["text"], bool(x.get("press_late")), bool(x.get("release_early")))
                          for x in play(m, "turn_taking", 3)[0]["lines"]) for m in ("normal", "ptt", "baton")}
        self.assertEqual(said["normal"], said["ptt"])
        self.assertEqual(said["normal"], said["baton"])

    def test_repeats_say_different_things(self):
        def texts(repeat):
            return [x["text"] for x in play("normal", "turn_taking", 1, repeat)[0]["lines"]]
        self.assertNotEqual(texts(0), texts(1))

    def test_every_line_has_its_fixture_and_a_long_line_is_long_enough(self):
        for repeat in range(5):
            self.assertGreater(line_for(0, 0, repeat, long=True)["seconds"], LONG_HOLD_S + 2)
            for who in range(3):
                for k in range(6):
                    self.assertGreater(line_for(who, k, repeat)["seconds"], .5)


class Clips(unittest.TestCase):
    def test_no_clip_keeps_a_silence_that_would_end_a_long_turn_where_clips_meet(self):
        for turn in (t for c in CONVERSATIONS for t in c):
            pcm = clip_pcm(turn.text)
            for edge in (pcm[:int(.2 * 24000) * 2], pcm[-int(.2 * 24000) * 2:]):
                self.assertGreater(max(abs(s) for s in array.array("h", edge)), 300, turn.text)


    def test_a_long_line_has_no_pause_that_would_end_the_turn(self):
        for repeat in range(3):
            for part in line_for(0, 0, repeat, long=True)["parts"]:
                pcm, longest, quiet = array.array("h", clip_pcm(part)), 0, 0
                for k in range(0, len(pcm), 480):
                    quiet = quiet + 20 if max(abs(s) for s in pcm[k:k + 480]) < 800 else 0
                    longest = max(longest, quiet)
                self.assertLess(longest, 300, part)


class Mic(unittest.TestCase):
    def _of(self, room, who="a"):
        return [c for c in room.calls if c[2] == who]

    def test_normal_never_touches_the_mic_or_the_floor(self):
        _, room = play("normal", "turn_taking", 2)
        self.assertEqual({c[1] for c in room.calls}, {"speak"})
        self.assertTrue(all(c[3:] == (0, 0) for c in room.calls), "normal plays every clip whole")

    def test_normal_still_records_the_slips_of_the_same_lines(self):
        tl, _ = play("normal", "turn_taking", 3)
        late = [bool(x.get("press_late")) for x in sorted(tl["lines"], key=lambda x: x["start"])]
        self.assertEqual(late, [hold_timing("turn_taking-3-0", k)["press_late"] for k in range(6)])

    def test_ptt_opens_before_the_first_frame_and_closes_after_the_last(self):
        tl, room = play("ptt", "turn_taking", 1)
        calls = self._of(room)[3:6]  # line 1: line 0 of this dialogue is a late press
        self.assertEqual([c[1] for c in calls], ["mic", "speak", "mic"])
        self.assertEqual((calls[0][3], calls[2][3]), (True, False))
        timing = hold_timing("turn_taking-1-0", 1)
        self.assertFalse(timing["press_late"] or timing["release_early"], "pick a line with no slip")
        _, press, release = tl["holds"][1]
        second = sorted(tl["lines"], key=lambda x: x["start"])[1]
        self.assertAlmostEqual(second["start"] - press, -timing["press_s"], delta=.03)
        self.assertAlmostEqual(release - second["end"], timing["release_s"], delta=.03)

    def _slipped(self, flag):
        for repeat in range(5):
            for k in range(6):
                if hold_timing(f"turn_taking-1-{repeat}", k)[flag]:
                    return repeat, k
        raise AssertionError(f"no {flag} in the script")

    def test_a_late_press_loses_the_start_of_the_clip(self):
        repeat, k = self._slipped("press_late")
        timing = hold_timing(f"turn_taking-1-{repeat}", k)
        tl, room = play("ptt", "turn_taking", 1, repeat)
        speak = [c for c in room.calls if c[1] == "speak"][k]
        self.assertAlmostEqual(speak[3], timing["press_s"], places=6)
        line = sorted(tl["lines"], key=lambda x: x["start"])[k]
        self.assertTrue(line["press_late"])

    def test_an_early_release_loses_the_end_of_the_clip(self):
        repeat, k = self._slipped("release_early")
        timing = hold_timing(f"turn_taking-1-{repeat}", k)
        tl, room = play("ptt", "turn_taking", 1, repeat)
        speak = [c for c in room.calls if c[1] == "speak"][k]
        self.assertAlmostEqual(speak[4], -timing["release_s"], places=6)
        self.assertTrue(sorted(tl["lines"], key=lambda x: x["start"])[k]["release_early"])

    def test_late_release_is_two_seconds_late_with_no_slip_to_excuse_it(self):
        for repeat in range(3):
            tl, _ = play("ptt", "late_release", 1, repeat)
            line = tl["lines"][0]
            self.assertAlmostEqual(tl["holds"][0][2] - line["end"], 2.0, delta=.03)
            self.assertFalse(line["release_early"])


class Baton(unittest.TestCase):
    def test_the_line_starts_on_the_grant_and_the_mic_opens_then(self):
        tl, room = play("baton", "overlap", 3)
        by_time = sorted(tl["floor"], key=lambda f: f[1])
        for line in tl["lines"]:
            grant = next(s for w, s, _ in by_time if w == line["who"])
            self.assertGreaterEqual(line["start"], grant)
        kinds = [c[1] for c in room.calls if c[2] == "c"]
        self.assertEqual(kinds, ["claim", "mic", "speak", "mic", "release"])

    def test_waiting_people_stay_shut_until_their_turn(self):
        tl, _ = play("baton", "overlap", 3)
        spans = sorted((x["start"], x["end"]) for x in tl["lines"])
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), spans)

    def test_a_claim_nobody_answers_is_a_line_never_heard(self):
        tl, room = play("baton", "turn_taking", 1, grants=False)
        self.assertNotIn("speak", [c[1] for c in room.calls])
        self.assertEqual(tl["holds"][0][0], "a")
        self.assertIn("never transcribed", " ".join(verdict("baton", "turn_taking", tl)["problems"]))

    def test_floor_spans_close_when_the_holder_changes_and_the_last_stays_open_only_if_held(self):
        tl, _ = play("baton", "walked_away", 2)
        self.assertEqual([f[0] for f in tl["floor"]], ["a", "b"])
        self.assertAlmostEqual(tl["floor"][0][2], tl["lines"][0]["end"] + IDLE_S, delta=.3)


class WalkedAway(unittest.TestCase):
    def test_nobody_lets_go(self):
        for mode in ("ptt", "baton"):
            tl, room = play(mode, "walked_away", 2)
            self.assertIsNone(tl["holds"][0][2], mode)
            self.assertNotIn(("a", False), [(c[2], c[3]) for c in room.calls if c[1] == "mic"], mode)
            self.assertNotIn("release", [c[1] for c in room.calls if c[2] == "a"], mode)


class Timeline(unittest.TestCase):
    def test_times_count_from_the_end_of_the_greeting(self):
        tl, _ = play("normal", "turn_taking", 1)
        self.assertTrue(0 <= min(x["start"] for x in tl["lines"]) < 1, "the first line follows the greeting")
        self.assertGreater(min(s for s, _ in tl["bot"]), 0, "the greeting isn't a reply")
        self.assertGreaterEqual(min(t for t, _, _ in tl["stored"]), 0)
        self.assertGreaterEqual(tl["end"], max(e for _, e in tl["bot"]))

    def test_bot_audio_makes_spans_and_a_pause_splits_them(self):
        loop = VirtualLoop()
        now = [0.0]
        run = Run("normal", "turn_taking", 1, 0, None, lambda: now[0])
        for t in (0.0, .02, .1, 2.0, 2.02):
            now[0] = t
            run.bot_audio()
        run.t0 = 0.0
        self.assertEqual(run.timeline([], [], [])["bot"], [(0.0, .1), (2.0, 2.02)])
        loop.close()

    def test_infra_signals_reach_the_verdict(self):
        loop = VirtualLoop()
        run = Run("normal", "turn_taking", 1, 0, None, loop.time)
        tl = run.timeline([], [], ["stt-cpu restart"])
        self.assertEqual(verdict("normal", "turn_taking", tl)["status"], "invalid")
        loop.close()


class RefuseUnlessLocal(unittest.TestCase):
    LOCAL = {"DATABASE_URL": "postgresql+asyncpg://meetlab:meetlab@postgres:5432/meetlab",
             "LIVEKIT_URL": "ws://transport-server:7880", "AGENT_RUNNER_URL": "http://agent-runner:7860"}

    def test_the_local_stack_is_fine(self):
        refuse_unless_local(self.LOCAL)
        refuse_unless_local({**self.LOCAL, "DATABASE_URL": "postgresql+asyncpg://m:m@localhost:5433/m",
                             "LIVEKIT_URL": "ws://localhost:7880", "AGENT_RUNNER_URL": "http://localhost:7860"})

    def test_anything_else_refuses_because_it_opens_the_modes_by_writing_the_database(self):
        for key, url in (("DATABASE_URL", "postgresql+asyncpg://m:m@meetlab-v2.abc.us-east-1.rds.amazonaws.com:5432/m"),
                         ("LIVEKIT_URL", "wss://meet-abc.livekit.cloud"),
                         ("AGENT_RUNNER_URL", "https://meet.wwbp.org")):
            with self.subTest(key), self.assertRaisesRegex(SystemExit, "refused"):
                refuse_unless_local({**self.LOCAL, key: url})

    def test_a_missing_address_refuses(self):
        with self.assertRaisesRegex(SystemExit, "refused"):
            refuse_unless_local({})


class Budget(unittest.TestCase):
    def test_an_attempt_costs_the_plans_matrix_over_its_attempts(self):
        self.assertEqual(attempt_usd("openai"), (4 / MATRIX_ATTEMPTS, 10 / MATRIX_ATTEMPTS))
        self.assertEqual(attempt_usd("elevenlabs"), (6 / MATRIX_ATTEMPTS, 50 / MATRIX_ATTEMPTS))
        self.assertEqual(attempt_usd("unknown"), attempt_usd("elevenlabs"))

    def test_a_budget_must_be_a_positive_number(self):
        self.assertEqual(budget("12.5"), 12.5)
        for bad in ("", "yes", "0", "-3", "nan", "inf"):
            with self.subTest(bad), self.assertRaisesRegex(SystemExit, "refused"):
                budget(bad)


class NoSuchCell(unittest.TestCase):
    def test_refused_before_anything_is_started(self):
        for mode, scenario, people in (("normal", "walked_away", 2), ("loud", "turn_taking", 1),
                                       ("ptt", "turn_taking", 9), ("ptt", "nonsense", 1)):
            with self.subTest(mode=mode, scenario=scenario, people=people), self.assertRaisesRegex(SystemExit, "refused"):
                asyncio.run(_one(mode, scenario, people, 0))


class Reruns(unittest.TestCase):
    RUNS = [{"scenario": "overlap", "people": 3, "repeat": r, "mode": "ptt"} for r in range(3)]

    def _go(self, statuses, cost_ok=100.0, done=(), per=1.0):
        attempts, saved = [], []
        left = list(statuses)

        async def attempt(run):
            attempts.append(run)
            return {**run, "status": left.pop(0)}
        out = asyncio.run(rerun_until_valid(self.RUNS, list(done), attempt, saved.append, cost_ok, per))
        return out, attempts, saved

    def test_an_invalid_attempt_reruns_under_the_same_key_and_is_kept(self):
        finished, attempts, saved = self._go(["invalid", "invalid", "pass", "fail", "pass"])
        self.assertTrue(finished)
        self.assertEqual([a["repeat"] for a in attempts], [0, 0, 0, 1, 2])
        self.assertEqual([s["status"] for s in saved], ["invalid", "invalid", "pass", "fail", "pass"])

    def test_a_failure_is_not_rerun(self):
        _, attempts, _ = self._go(["fail", "fail", "fail"])
        self.assertEqual(len(attempts), 3)

    def test_a_valid_earlier_row_is_not_paid_for_twice(self):
        done = [{**self.RUNS[0], "status": "pass"}, {**self.RUNS[1], "status": "invalid"}]
        _, attempts, _ = self._go(["pass", "pass"], done=done)
        self.assertEqual([a["repeat"] for a in attempts], [1, 2])

    def test_the_money_stops_it_loud_before_the_attempt_that_would_pass_the_budget(self):
        finished, attempts, _ = self._go(["invalid", "invalid", "invalid"], cost_ok=2.5)
        self.assertFalse(finished)
        self.assertEqual(len(attempts), 2)


class SttDown(unittest.TestCase):
    def _serve(self, body, delay=0):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                threading.Event().wait(delay)
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_ready_is_up(self):
        self.assertFalse(stt_down(self._serve(b'{"ready": true}')))

    def test_a_model_still_loading_is_a_restart(self):
        self.assertTrue(stt_down(self._serve(b'{"ready": false}')))

    def test_nobody_listening_is_a_restart(self):
        self.assertTrue(stt_down("http://127.0.0.1:9"))

    def test_merely_slow_is_not(self):
        self.assertFalse(stt_down(self._serve(b'{"ready": true}', delay=.5), timeout=.1))


if __name__ == "__main__":
    unittest.main()


class PrintResult(unittest.TestCase):
    def test_invalid_says_which_infrastructure_signal(self):
        # A single run keeps nothing on disk, so the printout is the only record of why it was invalid.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_result({"status": "invalid", "scenario": "turn_taking", "people": 1, "mode": "ptt",
                           "problems": [], "timeline": {"infra": ["lag"]}})
        self.assertIn("lag", out.getvalue())
