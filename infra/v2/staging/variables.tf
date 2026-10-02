variable "image_tag" {
  description = "Git SHA of the images to run; the pipeline passes the commit it just pushed"
  type        = string
}

variable "stt_nim_enabled" {
  description = "Run staging's Parakeet NIM (an on-demand g6.xlarge, $0.805/hour) and point bots at it; off = Deepgram"
  type        = bool
  default     = false # on for its first live test 2026-10-02 (#116, #117); passed
}

variable "bot_pool_min" {
  description = "Bot machines kept warm at all times (each $62/month); 0 on staging: tests and studies use Prepare for study"
  type        = number
  default     = 0
}

variable "bot_pool_max" {
  description = "Most bot machines at once (c6i.large, about 3 sessions each); a load test raises it"
  type        = number
  default     = 2
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
