output "instance_id" {
  value = aws_instance.app.id
}

output "public_ip" {
  value = var.create_elastic_ip ? aws_eip.app[0].public_ip : aws_instance.app.public_ip
}

output "public_dns" {
  value = aws_instance.app.public_dns
}

output "http_url" {
  value = "http://${var.create_elastic_ip ? aws_eip.app[0].public_ip : aws_instance.app.public_dns}"
}

output "site_address" {
  value = var.site_address
}

output "ssm_parameter_names" {
  value = [
    "${var.ssm_prefix}/AZURE_SPEECH_KEY",
    "${var.ssm_prefix}/AZURE_SPEECH_REGION",
    "${var.ssm_prefix}/AZURE_SPEECH_VOICE",
    "${var.ssm_prefix}/AZURE_SPEECH_FORMAT",
    "${var.ssm_prefix}/AZURE_STT_LANGUAGE",
    "${var.ssm_prefix}/AZURE_STT_CORRECTNESS_THRESHOLD",
  ]
}
