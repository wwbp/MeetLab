variable "image_tag" {
  description = "Git SHA of the images to run; the pipeline passes the commit it just pushed"
  type        = string
}

variable "stt_nim_enabled" {
  description = "Run staging's Parakeet NIM (an on-demand g6.xlarge, $0.805/hour) and point bots at it; off = Deepgram"
  type        = bool
  default     = true # on for the B3 100-room session (2026-10-04); off after
}

variable "model_services" {
  description = "Our own models to run, each on an on-demand g6.xlarge ($0.805/hour): llm (Qwen on vLLM), tts (Kokoro)"
  type        = set(string)
  default     = ["llm", "tts"] # on for the B3 100-room session (2026-10-04); off after
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
  default     = "c6i.xlarge" # raised for the B3 100-room session (2026-10-04); c6i.large after
}

variable "db_instance_class" {
  description = "The database's size; a big load test raises it (101 of ~180 connections at 60 rooms on db.t4g.small)"
  type        = string
  default     = "db.t4g.medium" # raised for the B3 100-room session (2026-10-04); db.t4g.small after
}

variable "tts_replicas" {
  description = "Voice (Kokoro) GPUs behind its load balancer, each a g6.xlarge; a big load test raises it (first audio 116 -> 260 ms from 24 to 60 rooms)"
  type        = number
  default     = 2 # raised for the B3 100-room session (2026-10-04); 1 after
  validation {
    condition     = var.tts_replicas >= 1 && var.tts_replicas <= 3
    error_message = "tts_replicas is 1 to 3"
  }
}

variable "bot_pool_max" {
  description = "Most bot machines at once (c6i.large, about 3 sessions each); a load test raises it"
  type        = number
  default     = 35 # raised for the B3 100-room session (~3 rooms per machine); 2 after
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
