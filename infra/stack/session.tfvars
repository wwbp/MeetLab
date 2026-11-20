# Session switches: production's sizes for one test window, on top of its profile (profiles.tf).
# The release applies this file (release.yml). Between windows every line here is a comment:
# a setting left in this file keeps costing money until a release removes it.
#
# 2026-11-19, load test of 100 rooms of 2-3 people on our own models (docs/load-testing.md),
# about $6 an hour while on:
model_services        = ["llm", "tts"]  # our LLM and voice, a g6.xlarge each
tts_replicas          = 2               # as the 100-room spike (2026-10-05)
stt_cpu_instance_type = "c6i.4xlarge"   # not measured: ~250 microphones, twice the capacity model's c6i.2xlarge for 100 mixed rooms
db_instance_class     = "db.t4g.medium" # 1.6 connections a room; t4g.small's limit is ~180
