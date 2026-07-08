variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "VPC to place the NIM instance in (the vivaprox VPC — same as the app + old T4)."
  type        = string
}

variable "subnet_id" {
  description = "Private subnet for the NIM instance (same subnet family as the old meetlab-stt-gpu)."
  type        = string
}

variable "agent_runner_sg_id" {
  description = "Security group of the agent-runner EB environment. NIM ports are opened ONLY to this SG."
  type        = string
}

variable "instance_type" {
  description = "GPU instance. g6.xlarge = L4 24GB, Compute Capability 8.9 (NIM requires >= 8.0)."
  type        = string
  default     = "g6.xlarge"
}

variable "root_volume_gb" {
  description = "Root EBS size. The NIM image (CUDA/TensorRT layers) is very large plus the DL AMI base and model cache — needs generous headroom."
  type        = number
  default     = 250
}

variable "nim_image" {
  description = "NVIDIA Parakeet-TDT NIM image."
  type        = string
  default     = "nvcr.io/nim/nvidia/parakeet-0.6b-tdt:latest"
}

variable "nim_tags_selector" {
  description = "Optional NIM model profile. Empty = English v2 (NVIDIA's documented default, no selector). Set 'type=multi' for the multilingual v3 model."
  type        = string
  default     = ""
}

variable "ngc_api_key_ssm_param" {
  description = "SSM Parameter Store (SecureString) name holding the NGC API key. Seeded by the deploy workflow from the NGC_API_KEY secret."
  type        = string
  default     = "/meetlab/stt-nim/ngc_api_key"
}

variable "private_zone_id" {
  description = "Optional Route53 private hosted zone id (must be associated with var.vpc_id) for a stable STT endpoint. Leave empty to skip DNS and use the instance private IP as NEMOTRON_STT_URL."
  type        = string
  default     = ""
}

variable "stt_dns_name" {
  description = "Stable private DNS name for the NIM (only used when private_zone_id is set)."
  type        = string
  default     = "stt-nim.meetlab.internal"
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default = {
    Project = "meetlab"
    Service = "stt-nim"
    Managed = "terraform"
  }
}
