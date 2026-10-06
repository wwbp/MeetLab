# Each environment's sizes, in one place. A variable left unset takes its environment's
# value; a load test sets one for a single run (the session switches, docs/load-testing.md).

locals {
  profiles = {
    # Staging between test runs: nothing warm, the smallest sizes.
    staging = {
      stt_nim_enabled     = false, model_services = [], tts_replicas = 1,
      bot_pool_min        = 0, bot_pool_max = 2, livekit_instance_type = "c6i.large",
      db_instance_class   = "db.t4g.small", db_multi_az = false, egress_count = 0,
      egress_room_cpu     = null, # LiveKit's default booking per recording; lowered only to measure
      livekit_self_hosted = true,
    }
    # Production (user's decisions, 2026-10-06, after the cost audit: docs/v2-infrastructure.md):
    # sized to use, scaled on request. One warm bot machine (3 rooms at once unprepared); Prepare
    # for study grows the pool to 40 machines (100 rooms). Media and video recording on LiveKit
    # Cloud (Ship: 100 recordings at once), so no LiveKit machine or recorder of our own. Speech-
    # to-text GPU off: switched on only for studies that need it. Database t4g.small (165
    # connections at 102 rooms) with a standby in a second zone. Paid LLM and voice.
    prod = {
      stt_nim_enabled   = false, model_services = [], tts_replicas = 1,
      bot_pool_min      = 1, bot_pool_max = 40, livekit_instance_type = "c6i.large",
      db_instance_class = "db.t4g.small", db_multi_az = true, egress_count = 0,
      egress_room_cpu   = null, livekit_self_hosted = false,
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
  livekit_self_hosted   = coalesce(var.livekit_self_hosted, local.profile.livekit_self_hosted)
}
