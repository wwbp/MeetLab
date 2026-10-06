# Each environment's sizes, in one place. A variable left unset takes its environment's
# value; a load test sets one for a single run (the session switches, docs/load-testing.md).

locals {
  profiles = {
    # Staging between test runs: nothing warm, the smallest sizes.
    staging = {
      stt_nim_enabled     = false, model_services = [], tts_replicas = 1,
      bot_pool_min        = 0, bot_pool_max = 12, livekit_instance_type = "c6i.large",
      db_instance_class   = "db.t4g.small", db_multi_az = false, egress_count = 0,
      egress_room_cpu     = null, # LiveKit's default booking per recording; lowered only to measure
      livekit_self_hosted = true, stt_cpu_enabled = true, stt_cpu_instance_type = "c6i.large",
      bot_cpu             = 512, bot_memory = 1024, bots_per_instance = 3, bot_instance_type = "c6i.large",
      container_insights  = true, # right-sizing sweep session (2026-10-06); off after
    }
    # Production (user's decisions, 2026-10-06, after the cost audit: docs/v2-infrastructure.md):
    # sized to use, scaled on request. One warm bot machine (3 rooms at once unprepared); Prepare
    # for study grows the pool to 40 machines (100 rooms). Media and video recording on LiveKit
    # Cloud (Ship: 100 recordings at once), so no LiveKit machine or recorder of our own. Speech-
    # to-text on CPU (Parakeet int8); the GPU only for studies past the measured switch point. Database t4g.small (165
    # connections at 102 rooms) with a standby in a second zone. Paid LLM and voice.
    prod = {
      stt_nim_enabled    = false, model_services = [], tts_replicas = 1,
      bot_pool_min       = 1, bot_pool_max = 40, livekit_instance_type = "c6i.large",
      db_instance_class  = "db.t4g.small", db_multi_az = true, egress_count = 0,
      egress_room_cpu    = null, livekit_self_hosted = false,
      stt_cpu_enabled    = true, stt_cpu_instance_type = "c6i.large", # speech-to-text for unscheduled sessions
      bot_cpu            = 512, bot_memory = 1024, bots_per_instance = 3, bot_instance_type = "c6i.large",
      container_insights = false,
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
  stt_cpu_enabled       = coalesce(var.stt_cpu_enabled, local.profile.stt_cpu_enabled)
  stt_cpu_instance_type = coalesce(var.stt_cpu_instance_type, local.profile.stt_cpu_instance_type)
  bot_cpu               = coalesce(var.bot_cpu, local.profile.bot_cpu)
  bot_memory            = coalesce(var.bot_memory, local.profile.bot_memory)
  bots_per_instance     = coalesce(var.bots_per_instance, local.profile.bots_per_instance)
  bot_instance_type     = coalesce(var.bot_instance_type, local.profile.bot_instance_type)
  container_insights    = coalesce(var.container_insights, local.profile.container_insights)
}
