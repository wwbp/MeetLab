# Test data

Every dataset MeetLab's tests use: what it is, where it lives, its licence, and how to rebuild
it. Large or licence-bound data stays out of git (the repository is public); what's here is
enough to rebuild it exactly.

| Dataset | Used by | Where it lives | Licence | Rebuild |
|---|---|---|---|---|
| Conversation library **v1**: 100 everyday dialogues (DailyDialog), voiced | load tests with `library = v1` | `s3://meetlab-v2-staging-media-…/loadtests/library/v1/` | CC BY-NC-SA 4.0 (research use) | [conversation-library/](conversation-library/) |
| Conversation library **v2**: 280 real, long chats (Topical-Chat), voiced | load tests with `library = v2` (the 100-room spike) | `s3://…/loadtests/library/v2/` | CDLA-Sharing-1.0 | [conversation-library/](conversation-library/) |
| Stored turns: 24 short recorded questions | load tests with `library = none` | in git: `agent-runner/tests/fixtures/conversations/` | ours (synthetic speech) | `make conversation-audio` (`agent-runner/tests/generate_conversation_audio.py`) |
| Spoken question | the live tests and the load generator | in git: `agent-runner/tests/fixtures/benchmark_prompt.wav` | ours (synthetic speech) | not rebuilt |
| Pilot participant audio | one-off analyses (e.g. smart turn, 2026-10-04) | **nowhere**: copied to a private scratch folder for the analysis, then deleted; only aggregates are kept | real participants | — |

**Rules for adding a dataset:** a row here; a folder with a `README.md` (source, pinned version,
licence, how it's selected and built) and a `SHA256SUMS` for its sources; and real people's
data never in git or in a shared copy.
