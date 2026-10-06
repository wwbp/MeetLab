"""Build the load test's conversation library once: pick the dialogues (conversation_library.py),
speak every line in its side's voice with Kokoro (open, run locally on CPU), put a pause in
the lines marked for one, and write the audio plus a manifest. The load generator reads it
from S3 (LIBRARY=s3://…/library.json); it stays out of the repo, which is public.

    uv run --no-project --with kokoro-onnx --with soundfile --with numpy --with boto3 \\
        python agent-runner/tests/build_conversation_library.py OUT_DIR [s3://bucket/prefix] [v1|v2]

v2 (the 100-room spike): Topical-Chat's held-out conversations (github.com/alexa/Topical-Chat,
conversations/test_freq.json and test_rare.json, CDLA-Sharing-1.0) at OUT_DIR/topicalchat/.

Needs ConvLab's DailyDialog (data.zip from huggingface.co/datasets/ConvLab/dailydialog, CC
BY-NC-SA 4.0) unpacked at OUT_DIR/dailydialog/dialogues.json, and Kokoro's model files
(github.com/thewh1teagle/kokoro-onnx releases, model-files-v1.0) in OUT_DIR/kokoro/.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conversation_library import paused, select, select_topical, split_for_pause, voice_for  # noqa: E402

RATE = 24000          # Kokoro's rate, and the load generator's microphone rate
PAUSE_S = 0.8         # a thinking pause: longer than the bot's endpointing (450 ms)
SOURCES = {
    "v1": "DailyDialog (Li et al., 2017), ConvLab mirror, CC BY-NC-SA 4.0; voices: Kokoro v1.0",
    "v2": "Topical-Chat (Gopalakrishnan et al., 2019), CDLA-Sharing-1.0; voices: Kokoro v1.0",
}


def dialogues(out: Path, version: str) -> list[dict]:
    if version == "v2":
        convs = {}
        for f in ("test_freq.json", "test_rare.json"):
            convs.update(json.loads((out / "topicalchat" / f).read_text()))
        return select_topical(convs, n=280)  # 100 rooms, a third of one person: 34×4 + 66×2 = 268
    return select(json.loads((out / "dailydialog" / "dialogues.json").read_text()), n=100)


def main(out: Path, upload: str | None, version: str = "v1"):
    VERSION = version
    from kokoro_onnx import Kokoro

    tts = Kokoro(str(out / "kokoro" / "kokoro-v1.0.onnx"), str(out / "kokoro" / "voices-v1.0.bin"))
    library = dialogues(out, VERSION)
    audio_dir = out / VERSION
    audio_dir.mkdir(parents=True, exist_ok=True)

    def say(text, voice):
        samples, rate = tts.create(text, voice=voice, speed=1.0, lang="en-us" if voice[0] == "a" else "en-gb")
        assert rate == RATE, rate
        return samples

    for d in library:
        for k, line in enumerate(d["lines"]):
            line["voice"] = voice_for(d["id"], line["side"])
            halves = split_for_pause(line["text"]) if paused(d["id"], k) else None
            line["paused"] = halves is not None
            line["audio"] = f"{d['id']}-{k}.wav"
            path = audio_dir / line["audio"]
            if not path.exists():
                parts = [say(h, line["voice"]) for h in halves] if halves else [say(line["text"], line["voice"])]
                gap = np.zeros(int(RATE * PAUSE_S), dtype=np.float32)
                wave = np.concatenate([p if i == 0 else np.concatenate([gap, p]) for i, p in enumerate(parts)])
                sf.write(path, wave, RATE, subtype="PCM_16")
        print(f"{d['id']}: {len(d['lines'])} lines", flush=True)

    manifest = {"version": VERSION, "rate": RATE, "pause_s": PAUSE_S,
                "source": SOURCES[VERSION],
                "dialogues": library}
    (audio_dir / "library.json").write_text(json.dumps(manifest, indent=1))
    lines = sum(len(d["lines"]) for d in library)
    print(f"{len(library)} dialogues, {lines} lines, {sum(l['paused'] for d in library for l in d['lines'])} with a pause")

    if upload:
        import boto3
        bucket, prefix = upload[5:].split("/", 1)
        s3 = boto3.client("s3")
        for f in sorted(audio_dir.iterdir()):
            s3.upload_file(str(f), bucket, f"{prefix.rstrip('/')}/{VERSION}/{f.name}")
        print(f"uploaded to {upload.rstrip('/')}/{VERSION}/library.json")


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None, sys.argv[3] if len(sys.argv) > 3 else "v1")
