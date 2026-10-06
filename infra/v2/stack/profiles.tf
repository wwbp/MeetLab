# Each environment's sizes, in one place. A variable left unset takes its environment's
# value; a load test sets one for a single run (the session switches, docs/load-testing.md).

locals {
  profiles = {
    # Staging between test runs: nothing warm, the smallest sizes.
    staging = {
      stt_nim_enabled   = false, model_services = [], tts_replicas = 1,
      bot_pool_min      = 0, bot_pool_max = 4, livekit_instance_type = "c6i.large",
      db_instance_class = "db.t4g.small", db_multi_az = false, egress_count = 1,
      egress_room_cpu   = 0.5, # LiveKit's default booking per recording; lowered only to measure
    }
    # Production (user's decisions, 2026-10-06): like v1 (Parakeet NIM always on, OpenAI
    # and ElevenLabs), the database across two zones, and 20 rooms at any time with nobody
    # pressing Prepare: 7 warm bot machines (3 sessions each), LiveKit c6i.large (60 rooms
    # at 57% CPU in L6). Prepare grows the bots to 40 machines (the spike's 100 rooms).
    # ponytail: one recording machine until a measured recording says how many 20 rooms need.
    prod = {
      stt_nim_enabled   = true, model_services = [], tts_replicas = 1,
      bot_pool_min      = 7, bot_pool_max = 40, livekit_instance_type = "c6i.large",
      db_instance_class = "db.t4g.medium", db_multi_az = true, egress_count = 1,
      egress_room_cpu   = null,
    }
  }
  profile = local.profiles[var.env]

  stt_nim_enabled       = coalesce(var.stt_nim_enabled, local.profile.stt_nim_enabled)
  model_services        = var.model_services != null ? var.model_services : toset(local.profile.model_services)
  tts_replicas          = coalesce(var.tts_replicas, local.profile.tts_replicas)
  bot_pool_min          = coalesce(var.bot_pool_min, local.profile.bot_pool_min)
  bot_pool_max          = coalesce(var.bot_pool_max, local.profile.bot_pool_max)
  livekit_instance_type = coalesce(var.livekit_instance_type, local.profile.livekit_instance_type)
  db_instance_class     = coalesce(var.db_instance_class, local.profile.db_instance_class)
  db_multi_az           = coalesce(var.db_multi_az, local.profile.db_multi_az)
  egress_count          = coalesce(var.egress_count, local.profile.egress_count)
  egress_room_cpu       = var.egress_room_cpu != null ? var.egress_room_cpu : local.profile.egress_room_cpu
}
