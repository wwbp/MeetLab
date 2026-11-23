# Session switches: production's sizes for one test window, on top of its profile (profiles.tf).
# The release applies this file (release.yml). Between windows every line here is a comment:
# a setting left in this file keeps costing money until a release removes it.
#
# ON since 2026-11-23: stage A, 5 rooms of 2-3 people on our own models, to read what v2.1.18's
# early transcription gained. Only what 5 rooms need; the speech machine as in the v2.1.16 runs
# so the numbers compare. Was on 2026-11-20 to 2026-11-22 for the 100-room window.
model_services        = ["llm", "tts"]  # our LLM and voice, a g6.xlarge each
stt_cpu_instance_type = "c6i.4xlarge"   # as the v2.1.16 runs
# tts_replicas          = 2               # as the 100-room spike (2026-10-05)
# db_instance_class     = "db.t4g.medium" # 1.6 connections a room; t4g.small's limit is ~180
