"""The database page's Bot Config form (/api/db) follows PUT /config's audio_mode rule:
global is normal only, a room's own row may be ptt or baton. Saving a row must not change its mode."""
import asyncio
import os
import re
import unittest

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")
# Importing runner builds the engine; nothing here connects to it.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:1/none")

from starlette.datastructures import FormData  # noqa: E402

from meetlab_api import runner  # noqa: E402

Form = asyncio.run(runner.BotConfigAdmin().scaffold_form())


def _browser_submits(select_html: str) -> str:
    """What a browser posts for a <select>: the selected option, or the first when none is."""
    options = re.findall(r"<option ([^>]*)>", select_html)
    chosen = next((o for o in options if "selected" in o), options[0])
    return re.search(r'value="([^"]*)"', chosen).group(1)


def _mode_is_valid(scope: str, mode: str) -> bool:
    form = Form(FormData({"scope": scope, "audio_mode": mode}))
    return form.audio_mode.validate(form)


class BotConfigAdminAudioModeTests(unittest.TestCase):
    def test_saving_a_room_row_keeps_its_audio_mode(self):
        for mode in ("ptt", "baton"):
            with self.subTest(mode=mode):
                # sqladmin's edit view: GET renders Form(data=<the row>), POST saves Form(<what was posted>).
                page = Form(data={"scope": "room-a", "audio_mode": mode})
                posted = _browser_submits(page.audio_mode())
                saved = Form(FormData({"scope": "room-a", "audio_mode": posted}))
                self.assertTrue(saved.audio_mode.validate(saved), saved.audio_mode.errors)
                self.assertEqual(saved.data["audio_mode"], mode)

    def test_validation_follows_put_config(self):
        for mode in runner._AUDIO_MODES_BUILT:
            with self.subTest(mode=mode):
                self.assertTrue(_mode_is_valid("room-a", mode))
                self.assertEqual(_mode_is_valid("global", mode), mode in runner._AUDIO_MODES_GLOBAL)
        self.assertFalse(_mode_is_valid("room-a", "open"))


if __name__ == "__main__":
    unittest.main()
