"""The load test's conversation library (conversation_library.py): which everyday dialogues
it uses, who in a room speaks which line, in which voice, and which lines carry a pause."""
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conversation_library import (  # noqa: E402
    VOICES, paused, participants_for, room_plan, select, select_topical, split_for_pause, voice_for,
)


def dialogue(i, topic="Work", turns=8, words=8, split="test"):
    return {"dialogue_id": f"dailydialog-{split}-{i}", "data_split": split, "domains": [topic],
            "turns": [{"utterance": " ".join(["word"] * words) + "."} for _ in range(turns)]}


class TestSelect(unittest.TestCase):
    def test_test_split_dialogues_of_six_to_ten_spoken_sized_turns(self):
        pool = [dialogue(1), dialogue(2, turns=4), dialogue(3, turns=12), dialogue(4, words=40),
                dialogue(5, split="train"), dialogue(6, words=1)]
        self.assertEqual([d["id"] for d in select(pool, n=10)], ["dailydialog-test-1"])

    def test_topics_are_spread_not_just_the_commonest(self):
        pool = [dialogue(i, "Relationship") for i in range(50)] + [dialogue(100 + i, "Health") for i in range(5)]
        topics = Counter(d["topic"] for d in select(pool, n=10))
        self.assertEqual(topics["Health"], 5)

    def test_the_same_input_gives_the_same_library(self):
        pool = [dialogue(i, t) for i, t in enumerate(["Work", "Health", "Finance"] * 20)]
        self.assertEqual(select(pool, n=12), select(list(reversed(pool)), n=12))

    def test_lines_keep_who_said_them(self):
        lines = select([dialogue(1)], n=1)[0]["lines"]
        self.assertEqual([l["side"] for l in lines], [0, 1] * 4)


class TestRooms(unittest.TestCase):
    def test_half_the_rooms_have_one_person_30_percent_two_20_percent_three(self):
        self.assertEqual(Counter(participants_for(i) for i in range(100)), {1: 50, 2: 30, 3: 20})

    def test_even_a_small_run_has_every_size_of_room(self):
        self.assertEqual(sorted({participants_for(i) for i in range(4)}), [1, 2, 3])

    def test_one_person_speaks_one_side_and_the_bot_answers_in_place_of_the_other(self):
        lib = select([dialogue(1), dialogue(2)], n=2)
        plan = room_plan(0, lib)  # room 0: one person, one side of each of two dialogues
        self.assertEqual({p for p, _ in plan}, {0})
        self.assertEqual({line["side"] for _, line in plan}, {0})
        self.assertEqual(len(plan), 2 * 4)

    def test_two_people_speak_the_two_sides(self):
        lib = select([dialogue(1)], n=1)
        plan = room_plan(1, lib)  # room 1: two people
        self.assertEqual([p for p, _ in plan], [0, 1] * 4)

    def test_three_people_take_the_lines_in_turn(self):
        lib = select([dialogue(1)], n=1)
        plan = room_plan(3, lib)  # room 3: three people
        self.assertEqual([p for p, _ in plan][:6], [0, 1, 2, 0, 1, 2])


class TestVoicesAndPauses(unittest.TestCase):
    def test_a_dialogue_side_always_has_the_same_voice_and_sides_differ(self):
        self.assertEqual(voice_for("d-1", 0), voice_for("d-1", 0))
        self.assertNotEqual(voice_for("d-1", 0), voice_for("d-1", 1))
        self.assertTrue(set(voice_for(f"d-{i}", s) for i in range(40) for s in (0, 1)) <= set(VOICES))
        self.assertGreater(len({voice_for(f"d-{i}", 0) for i in range(40)}), 10)

    def test_about_a_fifth_of_lines_carry_a_mid_sentence_pause(self):
        share = sum(paused(f"d-{i}", n) for i in range(50) for n in range(8)) / 400
        self.assertTrue(0.15 <= share <= 0.25, share)

    def test_a_pause_falls_at_a_comma_or_a_joining_word_near_the_middle(self):
        self.assertEqual(split_for_pause("Sure, I can meet you after work tomorrow."),
                         ("Sure,", "I can meet you after work tomorrow."))
        self.assertEqual(split_for_pause("I like the blue one but the red is cheaper."),
                         ("I like the blue one", "but the red is cheaper."))
        self.assertIsNone(split_for_pause("Thanks."))

    def test_or_between_two_sentences_of_one_turn(self):
        self.assertEqual(split_for_pause("That's unless there is a traffic jam. It could take three hours."),
                         ("That's unless there is a traffic jam.", "It could take three hours."))


def topical(cid, turns=20, words=12):
    """A Topical-Chat conversation as its JSON has it: two agents taking turns."""
    return cid, {"content": [{"agent": f"agent_{1 + k % 2}", "message": " ".join(["word"] * words) + "."}
                             for k in range(turns)]}


class TestTopicalChat(unittest.TestCase):
    """The 100-room spike's library (user, 2026-10-05): real, long human-to-human chats
    (Topical-Chat, CDLA-Sharing-1.0), enough to fill 10 minutes per room without repeating."""

    def test_long_spoken_sized_conversations_in_a_fixed_order(self):
        convs = dict([topical("b"), topical("a"), topical("short", turns=10), topical("wordy", words=60),
                      topical("terse", words=1)])
        self.assertEqual([d["id"] for d in select_topical(convs, n=10)], ["tc-a", "tc-b"])

    def test_lines_keep_who_said_them(self):
        lines = select_topical(dict([topical("a")]), n=1)[0]["lines"]
        self.assertEqual([line["side"] for line in lines], [0, 1] * 10)

    def test_equal_mix_has_as_many_rooms_of_one_two_and_three(self):
        self.assertEqual(Counter(participants_for(i, "equal") for i in range(99)), {1: 33, 2: 33, 3: 33})
        self.assertEqual(sorted({participants_for(i, "equal") for i in range(3)}), [1, 2, 3])

    def test_each_room_chains_its_own_conversations_and_no_two_rooms_share_one(self):
        lib = select_topical(dict(topical(f"c{i:03d}") for i in range(45)), n=45)  # 4 one-person rooms take 6
        rooms = [room_plan(r, lib, per_room=3, mix="equal") for r in range(10)]
        heard = [{line["text"] + str(id(line)) for _, line in plan} for plan in rooms]
        for a in range(10):
            for b in range(a + 1, 10):
                self.assertFalse(heard[a] & heard[b], (a, b))
        self.assertEqual(len(rooms[1]), 3 * 20)  # room 1: two people, three whole conversations

    def test_one_person_rooms_get_twice_the_conversations(self):
        # One person speaks only one side (the bot answers the other): twice the conversations
        # fill the same time; still no conversation shared between rooms.
        lib = select_topical(dict(topical(f"c{i:03d}") for i in range(40)), n=40)
        plans = [room_plan(r, lib, per_room=3, mix="equal") for r in range(9)]
        self.assertEqual(len(plans[0]), 6 * 10)  # room 0: one person, six conversations' one side
        heard = [{id(line) for _, line in plan} for plan in plans]
        self.assertEqual(sum(map(len, heard)), len(set().union(*heard)))


if __name__ == "__main__":
    unittest.main()
