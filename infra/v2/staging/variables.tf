variable "image_tag" {
  description = "Git SHA of the images to run; the pipeline passes the commit it just pushed"
  type        = string
}

variable "stt_nim_enabled" {
  description = "Run staging's Parakeet NIM (an on-demand g6.xlarge, $0.805/hour) and point bots at it; off = Deepgram"
  type        = bool
  default     = false # off between test runs; on for L3, the comparisons, L6, B2/B3, B4 (2026-10-02/05)
}

variable "model_services" {
  description = "Our own models to run, each on an on-demand g6.xlarge ($0.805/hour): llm (Qwen on vLLM), tts (Kokoro)"
  type        = set(string)
  default     = [] # off between test runs; on for L3, the comparisons, L6, B2/B3, B4 (2026-10-02/05)
  validation {
    condition     = length(setsubtract(var.model_services, ["llm", "tts"])) == 0
    error_message = "model_services names llm and/or tts"
  }
}

variable "bot_pool_min" {
  description = "Bot machines kept warm at all times (each $62/month); 0 on staging: tests and studies use Prepare for study"
  type        = number
  default     = 0
}

variable "livekit_instance_type" {
  description = "Self-hosted LiveKit's machine; a big load test raises it (57% CPU on c6i.large at 60 rooms)"
  type        = string
  default     = "c6i.large" # c6i.xlarge for the B2/B3 sessions (2026-10-04)
}

variable "db_instance_class" {
  description = "The database's size; a big load test raises it (101 of ~180 connections at 60 rooms on db.t4g.small)"
  type        = string
  default     = "db.t4g.small" # db.t4g.medium (B2) or db.t3.medium (B3, when t4g had no capacity) for sessions (2026-10-04)
}

variable "tts_replicas" {
  description = "Voice (Kokoro) GPUs behind its load balancer, each a g6.xlarge; a big load test raises it (first audio 116 -> 260 ms from 24 to 60 rooms)"
  type        = number
  default     = 1 # 2 for the B2/B3 sessions (2026-10-04)
  validation {
    condition     = var.tts_replicas >= 1 && var.tts_replicas <= 3
    error_message = "tts_replicas is 1 to 3"
  }
}

variable "bot_pool_max" {
  description = "Most bot machines at once (c6i.large, about 3 sessions each); a load test raises it"
  type        = number
  default     = 2 # a load test raises it (35 for B2/B3, 12 for B4, 2026-10-04/05)
}

variable "container_insights" {
  description = "Per-task CPU and memory metrics (billed per task); on for load tests"
  type        = bool
  default     = false
}

variable "livekit_self_hosted" {
  description = "Run our own LiveKit (livekit.tf) and point meet, the runner and bots at it; off = LiveKit Cloud"
  type        = bool
  default     = true # on for load-test readiness (2026-10-02); LiveKit Cloud when off
}

variable "egress_count" {
  description = "Egress machines recording video on our own LiveKit (egress_server.tf); 0 between studies"
  type        = number
  default     = 1 # on for the first video test on our own LiveKit (2026-10-05); 0 after
}
