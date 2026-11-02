"""The voice speaks only the "reply" of the model's JSON answer (meetlab_core.turns), as it streams.

The LLM streams {"reply": "...", "addressed_to": [...]} in arbitrary chunks. ReplyText pulls the
decoded characters of the top-level "reply" string out as they arrive, so speech still starts on
the first clause; ReplyStreamer sits between the LLM and TTS and, at the end of each response,
hands the whole parsed answer (parse_reply, untrusted) to on_reply.
"""
import json
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    Frame, InterruptionFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from meetlab_core.turns import BotReply, parse_reply


def _is_high_surrogate(ch: str) -> bool:
    return "\ud800" <= ch <= "\udbff"


class ReplyText:
    """Incremental extractor of the top-level "reply" string of a streamed JSON object.

    A first non-space character other than "{" means a model without structured output: the text
    passes through unchanged."""

    def __init__(self):
        self._raw: list[str] = []
        self._mode = None        # None until the first non-space character, then "json" or "pass"
        self._depth = 0
        self._in_str = False
        self._esc = ""           # an escape sequence in progress, from its backslash
        self._high = ""          # a decoded high surrogate waiting for its low half
        self._expect_key = False  # at depth 1: the next string is a key
        self._key: list[str] | None = None  # the key being read (depth 1)
        self._last_key = None
        self._capture = False    # inside the reply string
        self._done = False       # the reply string has ended

    @property
    def text(self) -> str:
        return "".join(self._raw)

    def feed(self, chunk: str) -> str:
        self._raw.append(chunk)
        if self._mode == "pass":
            return chunk
        if self._mode is None:
            stripped = chunk.lstrip()
            if not stripped:
                return ""
            if stripped[0] != "{":
                self._mode = "pass"
                return self.text  # with the leading whitespace held back so far
            self._mode = "json"
        out = []
        for ch in chunk:
            self._char(ch, out)
        return "".join(out)

    def _char(self, ch: str, out: list[str]) -> None:
        if self._in_str:
            if self._esc:
                self._esc += ch
                if self._esc[1] == "u" and len(self._esc) < 6:
                    return
                decoded, self._esc = json.loads(f'"{self._esc}"'), ""
            elif ch == "\\":
                self._esc = ch
                return
            elif ch == '"':
                self._end_string()
                return
            else:
                decoded = ch
            if self._high:  # join a surrogate pair split over two escapes; a lone half is dropped
                if "\udc00" <= decoded <= "\udfff":
                    decoded = (self._high + decoded).encode("utf-16", "surrogatepass").decode("utf-16")
                self._high = ""
            if _is_high_surrogate(decoded):
                self._high = decoded
                return
            if self._key is not None:
                self._key.append(decoded)
            elif self._capture:
                out.append(decoded)
            return
        if ch == '"':
            self._in_str = True
            if self._depth == 1 and self._expect_key:
                self._key = []
            elif self._depth == 1 and self._last_key == "reply" and not self._done:
                self._capture = True
        elif ch in "{[":
            self._depth += 1
            if self._depth == 1:
                self._expect_key = True
        elif ch in "}]":
            self._depth -= 1
        elif self._depth == 1 and ch == ",":
            self._expect_key = True
        elif self._depth == 1 and ch == ":":
            self._expect_key = False

    def _end_string(self) -> None:
        self._in_str = False
        if self._key is not None:
            self._last_key, self._key = "".join(self._key), None
        elif self._capture:
            self._capture, self._done = False, True


class ReplyStreamer(FrameProcessor):
    """Between the LLM and TTS: LLMTextFrames carry only the reply's characters; at each
    response's end, on_reply gets the parsed answer (who it was addressed to)."""

    def __init__(self, on_reply: Callable[[BotReply], Awaitable[None]] | None = None):
        super().__init__()
        self._on_reply = on_reply
        self._reply = ReplyText()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, (LLMFullResponseStartFrame, InterruptionFrame)):
                self._reply = ReplyText()
            elif isinstance(frame, LLMTextFrame):
                text = self._reply.feed(frame.text)
                if text:
                    await self.push_frame(LLMTextFrame(text), direction)
                return
            elif isinstance(frame, LLMFullResponseEndFrame):
                raw, self._reply = self._reply.text, ReplyText()
                if raw and self._on_reply:  # nothing left after an interruption: nothing said
                    await self._on_reply(parse_reply(raw))
        await self.push_frame(frame, direction)
