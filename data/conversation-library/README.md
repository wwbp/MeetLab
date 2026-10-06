# Conversation library

What the load test's synthetic participants say. Each line of a real dialogue is voiced once
(Kokoro, an open text-to-speech model, run on a laptop) and stored in S3; the load generator
reads it from there. Every run speaks the same lines in the same voices, so runs compare.

## Versions

| | v1 | v2 |
|---|---|---|
| Source | [DailyDialog](https://huggingface.co/datasets/ConvLab/dailydialog) (Li et al., 2017), ConvLab mirror, revision `745c179` | [Topical-Chat](https://github.com/alexa/Topical-Chat) (Gopalakrishnan et al., 2019), commit `7c93922` |
| Licence | CC BY-NC-SA 4.0 (research use) | CDLA-Sharing-1.0 |
| Kind of talk | everyday two-person dialogues | real human-to-human chats about a topic |
| Selection (`agent-runner/conversation_library.py`) | `select`: test split, 6–10 turns, lines of 2–30 words, topics taken in turn; 100 dialogues | `select_topical`: held-out (test) conversations, 16+ turns, lines of 2–40 words, fixed order; 280 conversations |
| Size | 100 dialogues, 795 lines | 280 conversations, ~6,100 lines |
| Used for | the load tests up to 2026-10-04 (L6, B2, B3) | the 100-room spike (10-minute holds), 2026-10-05 |
| S3 | `loadtests/library/v1/` | `loadtests/library/v2/` |
| Manifest `library.json` SHA-256 | `b3e07bd581dab34130ef8df6909441750e7d6b33a574c76430285b6b41876582` | written when v2 is built (see Verify) |

Both: the two sides of a dialogue get different voices (20 Kokoro English voices); about one
line in five is spoken with a 0.8 s pause mid-sentence, to test turn-taking. In a room of one
person the bot answers in place of the other side; two people speak the two sides; three take
the lines in turn. With `mix = equal` a third of rooms have each size (default: the study
mix, 50/30/20). In v2 each room speaks its own conversations back to back, never another
room's.

## Rebuild

```bash
# 1. Sources, pinned and checked against SHA256SUMS (~400 MB)
data/conversation-library/fetch.sh ~/meetlab-library

# 2. Voice every line and write library.json (Kokoro on a laptop CPU, ~47 lines/min: v1 ~20 min, v2 ~2 h)
uv run --no-project --with kokoro-onnx --with soundfile --with numpy --with boto3 \
  python agent-runner/tests/build_conversation_library.py ~/meetlab-library "" v2

# 3. Upload (needs write access to the media bucket; log it in infra/v2/LEDGER.md, Manual actions)
uv run --no-project --with kokoro-onnx --with soundfile --with numpy --with boto3 \
  python agent-runner/tests/build_conversation_library.py ~/meetlab-library \
  s3://meetlab-v2-staging-media-<account>/loadtests/library v2
```

The builder skips lines already voiced, so step 3 after step 2 only uploads.

## Verify

The selection is deterministic: the same sources give the same dialogues and lines (checked
2026-10-05: v1 re-selected from freshly fetched sources matched the library in S3 exactly).

```bash
shasum -a 256 ~/meetlab-library/v1/library.json   # must match the table above
```

## Use

In the **Load test v2** workflow: `library = v1` or `v2`, and `mix = study` or `equal`.
How load tests work and what the report means: [docs/load-testing.md](../../docs/load-testing.md).
