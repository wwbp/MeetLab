variable "image_tag" {
  description = "Git SHA of the images to run; the pipeline passes the commit it just pushed"
  type        = string
}

variable "stt_nim_enabled" {
  description = "Run staging's Parakeet NIM (a spot g6.xlarge) and point bots at it; off = Deepgram"
  type        = bool
  default     = true # on for its first live test (2026-10-02); off again after
}
