output "instance_id" {
  description = "EC2 instance id (SSM Run Command target for the deploy workflow)."
  value       = aws_instance.nim.id
}

output "private_ip" {
  description = "NIM private IP."
  value       = aws_instance.nim.private_ip
}

output "stt_url" {
  description = "Stable NIM endpoint — set as NEMOTRON_STT_URL (with NEMOTRON_STT_API=openai)."
  value       = "http://${var.stt_dns_name}:9000"
}
