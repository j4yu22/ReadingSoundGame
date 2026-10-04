variable "aws_region" {
  type    = string
  default = "us-west-2"
}

variable "expected_account_id" {
  description = "Optional account guard; provider refuses to operate in any other account when set."
  type        = string
  default     = null
  validation {
    condition     = var.expected_account_id == null ? true : can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "Expected account ID must contain exactly 12 digits."
  }
}

variable "project_name" {
  type    = string
  default = "reading-sound-game"
}

variable "environment" {
  type    = string
  default = "production"
  validation {
    condition     = contains(["production", "staging"], var.environment)
    error_message = "Use a separate production or staging stack and state key."
  }
}

variable "vpc_id" {
  description = "Existing EC2 VPC; this stack does not replace the website instance."
  type        = string
}

variable "private_db_subnet_ids" {
  description = "Existing private subnets in this VPC across at least two AZs. Use this OR private_db_subnets."
  type        = set(string)
  default     = []
  validation {
    condition = (
      (length(var.private_db_subnet_ids) >= 2 && length(var.private_db_subnets) == 0) ||
      (length(var.private_db_subnet_ids) == 0 && length(var.private_db_subnets) >= 2)
    )
    error_message = "Provide either at least two existing private subnet IDs OR at least two new private_db_subnets, never both."
  }
}

variable "private_db_subnets" {
  description = "Optional private subnets to create, keyed by a stable short label, with reviewed unused VPC CIDRs and distinct AZs."
  type = map(object({
    cidr_block        = string
    availability_zone = string
  }))
  default = {}
  validation {
    condition     = alltrue([for subnet in var.private_db_subnets : can(cidrnetmask(subnet.cidr_block))])
    error_message = "Each new subnet needs a valid IPv4 CIDR within the existing VPC; AWS rejects overlaps."
  }
  validation {
    condition     = length(var.private_db_subnets) == 0 || length(toset([for subnet in var.private_db_subnets : subnet.availability_zone])) >= 2
    error_message = "New private database subnets must span at least two distinct AZs."
  }
}

variable "app_security_group_id" {
  description = "Existing EC2 security group allowed to connect to PostgreSQL."
  type        = string
}

variable "app_iam_role_name" {
  description = "Existing EC2 instance role; granted runtime DB access and parent identity retention in this pool."
  type        = string
}

variable "migrator_iam_role_name" {
  description = "Separate migration role name, existing or created with trusted_migration_operator_arn; never the EC2 runtime role."
  type        = string
  validation {
    condition     = var.migrator_iam_role_name != var.app_iam_role_name
    error_message = "Use a separate migration role, never the website runtime role."
  }
}

variable "trusted_migration_operator_arn" {
  description = "Optional exact IAM user ARN to create the migrator role with MFA-required trust; null uses an existing migration role."
  type        = string
  default     = null
  validation {
    condition     = var.trusted_migration_operator_arn == null ? true : can(regex("^arn:aws:iam::[0-9]{12}:user/[A-Za-z0-9+=,.@_/-]+$", var.trusted_migration_operator_arn))
    error_message = "Specify one exact IAM user ARN; wildcard, account-root, and website-role trust are not accepted."
  }
}

variable "site_origin" {
  description = "HTTPS site origin, without a path or trailing slash. No wildcard redirects."
  type        = string
  validation {
    condition     = can(regex("^https://[a-zA-Z0-9.-]+(:443)?$", var.site_origin))
    error_message = "Use an exact HTTPS origin, e.g. https://readingsoundgames.com."
  }
}

variable "cognito_domain_prefix" {
  description = "Globally unique Cognito managed-login domain prefix."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$", var.cognito_domain_prefix))
    error_message = "Choose a unique 2-63 character lowercase domain prefix."
  }
}

variable "parent_signup_enabled" {
  description = "Keep false during infrastructure review; enable only for reviewed parent onboarding."
  type        = bool
  default     = false
}

variable "ses_identity_arn" {
  description = "Verified SES identity ARN for production auth emails; null uses Cognito's limited test sender."
  type        = string
  default     = null
}

variable "auth_email_from" {
  description = "Verified SES sender, required with ses_identity_arn."
  type        = string
  default     = null
  validation {
    condition     = (var.ses_identity_arn == null) == (var.auth_email_from == null)
    error_message = "Provide both SES identity ARN and sender, or leave both null."
  }
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "privacy_email_identity_arn" {
  description = "Verified SES sender identity for privacy notifications; no email permission when null."
  type        = string
  default     = null
}

variable "privacy_email_from" {
  description = "Exact verified SES sender for privacy notifications."
  type        = string
  default     = null
  validation {
    condition     = (var.privacy_email_identity_arn == null) == (var.privacy_email_from == null)
    error_message = "Provide both privacy SES identity ARN and exact sender, or leave both null."
  }
}

variable "multi_az" {
  description = "A standby improves availability and adds cost."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Only disable for a separately reviewed infrastructure retirement."
  type        = bool
  default     = true
}

variable "final_snapshot_suffix" {
  description = "Use a new suffix before a reviewed DB retirement; snapshots need their own expiry policy."
  type        = string
  default     = "retirement"
}
