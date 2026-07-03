variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-west-2"
}

variable "project_name" {
  description = "Name prefix for AWS resources."
  type        = string
  default     = "reading-sound-game"
}

variable "instance_type" {
  description = "EC2 instance type for the Docker host."
  type        = string
  default     = "t3.micro"
}

variable "key_name" {
  description = "Existing EC2 key pair name for SSH. Leave blank to disable SSH key assignment."
  type        = string
  default     = ""
}

variable "ssh_cidr_blocks" {
  description = "CIDR blocks allowed to SSH to the instance. Use your IP/32 when possible."
  type        = list(string)
  default     = []
}

variable "repository_url" {
  description = "Git repository URL the EC2 host will clone."
  type        = string
}

variable "repository_ref" {
  description = "Git branch, tag, or commit to deploy."
  type        = string
  default     = "main"
}

variable "site_address" {
  description = "Caddy site address. Use a real domain for HTTPS, or :80 for HTTP-only testing."
  type        = string
  default     = ":80"
}

variable "acme_email" {
  description = "Email for Let's Encrypt / ACME certificate notices."
  type        = string
  default     = ""
}

variable "ssm_prefix" {
  description = "SSM Parameter Store prefix containing Azure speech settings."
  type        = string
  default     = "/reading-sound-game"
}

variable "docker_compose_version" {
  description = "Docker Compose CLI plugin version installed by user data."
  type        = string
  default     = "v5.1.2"
}

variable "docker_buildx_version" {
  description = "Docker Buildx CLI plugin version installed by user data."
  type        = string
  default     = "v0.35.0"
}

variable "create_elastic_ip" {
  description = "Create and attach a static Elastic IP. Useful for DNS, but remember public IPv4 addresses can cost money."
  type        = bool
  default     = false
}

variable "auto_update_interval_minutes" {
  description = "How often the EC2 host checks GitHub for updates. Set to 0 to disable automatic updates."
  type        = number
  default     = 5
}
