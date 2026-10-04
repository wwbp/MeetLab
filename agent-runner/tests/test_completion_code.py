"""The completion code, recomputed in Python exactly as meet makes it (meet/lib/completion-code.ts):
the live study-flow test checks the code a participant gets, and a researcher can check codes from
the Prolific export. Pinned to the same example as meet/lib/completion-code.test.ts."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from acceptance_staging import completion_code  # noqa: E402


class TestCompletionCode(unittest.TestCase):
    def test_the_same_code_meet_gives(self):
        self.assertEqual(completion_code("study-7", "5f2a91b3c4d5e6f708192a3b", "test-secret"), "Y7MH4UK6")

    def test_differs_by_room(self):
        self.assertNotEqual(completion_code("study-8", "5f2a91b3c4d5e6f708192a3b", "test-secret"), "Y7MH4UK6")


if __name__ == "__main__":
    unittest.main()
