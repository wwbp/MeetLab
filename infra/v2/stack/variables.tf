variable "env" {
  description = "Which environment this stack is: staging or prod. Names, logs, secrets and the state file follow it"
  type        = string
  default     = "staging"
  validation {
    condition     = contains(["staging", "prod"], var.env)
    error_message = "env is staging or prod"
  }
}

variable "hostname_suffix" {
  description = "Public names are meet<suffix>, livekit<suffix> and turn<suffix>.wwbp.org: -staging; -v2 for production until cutover, then empty"
  type        = string
  default     = "-staging"
}

variable "image_tag" {
  description = "Git SHA of the images to run; the pipeline passes the commit it just pushed"
  type        = string
}

variable "stt_nim_enabled" {
  description = "Run staging's Parakeet NIM (an on-demand g6.xlarge, $0.805/hour) and point bots at it; off = Deepgram"
  type        = bool
  default     = null # the profile (profiles.tf); a load test switches it on
}

variable "model_services" {
  description = "Our own models to run, each on an on-demand g6.xlarge ($0.805/hour): llm (Qwen on vLLM), tts (Kokoro)"
  type        = set(string)
  default     = null # the profile (profiles.tf); a load test switches them on
  validation {
    condition     = var.model_services == null || length(setsubtract(coalesce(var.model_services, []), ["llm", "tts"])) == 0
    error_message = "model_services names llm and/or tts"
  }
}

variable "bot_pool_min" {
  description = "Bot machines kept warm at all times (each $62/month); 0 on staging: tests and studies use Prepare for study"
  type        = number
  default     = null # the profile (profiles.tf)
}

variable "livekit_instance_type" {
  description = "Self-hosted LiveKit's machine; a big load test raises it (57% CPU on c6i.large at 60 rooms)"
  type        = string
  default     = null # the profile (profiles.tf); c6i.xlarge for B2/B3 and the 100-room spike
}

variable "db_instance_class" {
  description = "The database's size; a big load test raises it (101 of ~180 connections at 60 rooms on db.t4g.small)"
  type        = string
  default     = null # the profile (profiles.tf); db.t3.medium for B3 and the 100-room spike
}

variable "tts_replicas" {
  description = "Voice (Kokoro) GPUs behind its load balancer, each a g6.xlarge; a big load test raises it (first audio 116 -> 260 ms from 24 to 60 rooms)"
  type        = number
  default     = null # the profile (profiles.tf); 2 for B2/B3 and the 100-room spike
  validation {
    condition     = var.tts_replicas == null || (coalesce(var.tts_replicas, 1) >= 1 && coalesce(var.tts_replicas, 1) <= 3)
    error_message = "tts_replicas is 1 to 3"
  }
}

variable "bot_pool_max" {
  description = "Most bot machines at once (c6i.large, about 3 sessions each); a load test raises it"
  type        = number
  default     = null # the profile (profiles.tf); 40 for the 100-room spike
}

variable "container_insights" {
  description = "Per-task CPU and memory metrics (billed per task); on for load tests"
  type        = bool
  default     = false
}

variable "livekit_self_hosted" {
  description = "Run our own LiveKit (livekit.tf) and point meet, the runner and bots at it; off = LiveKit Cloud"
  type        = bool
  default     = null # the profile (profiles.tf): staging self-hosted, production LiveKit Cloud
}

variable "egress_count" {
  description = "Egress machines recording video on our own LiveKit (egress_server.tf); 0 between studies"
  type        = number
  default     = null # the profile (profiles.tf); first video test passed 2026-10-05, #191
}

variable "db_multi_az" {
  description = "A standby database in a second zone, taking over in 1-2 minutes if one fails"
  type        = bool
  default     = null # the profile (profiles.tf)
}

variable "egress_room_cpu" {
  description = "CPU the recorder books per room recording (LiveKit's default 4); lowered only to measure how many recordings one machine really holds"
  type        = number
  default     = null # the profile (profiles.tf): LiveKit's default
}

variable "stt_cpu_enabled" {
  description = "Run speech-to-text on CPU (stt_cpu.tf, Parakeet int8) and point bots at it; the GPU NIM wins when also on"
  type        = bool
  default     = null # the profile (profiles.tf)
}

variable "stt_cpu_instance_type" {
  description = "The CPU speech-to-text machine; sized by the switch-point run"
  type        = string
  default     = null # the profile (profiles.tf)
}

variable "bot_cpu" {
  description = "CPU units each bot reserves (1024 = 1 vCPU); set by the right-sizing sweep"
  type        = number
  default     = null # the profile (profiles.tf)
}

variable "bot_memory" {
  description = "Memory each bot reserves, MB; set by the right-sizing sweep"
  type        = number
  default     = null # the profile (profiles.tf)
}

variable "bots_per_instance" {
  description = "Bots Prepare for study packs onto one bot machine; must fit the machine at the bot's reservation"
  type        = number
  default     = null # the profile (profiles.tf)
}

variable "bot_instance_type" {
  description = "The bot machines' type; set by the right-sizing sweep"
  type        = string
  default     = null # the profile (profiles.tf)
}
