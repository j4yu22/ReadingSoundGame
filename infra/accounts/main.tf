data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_subnet" "database" {
  for_each = var.private_db_subnet_ids
  id       = each.value
}

data "aws_security_group" "app" {
  id = var.app_security_group_id
}

locals {
  name             = "${var.project_name}-${var.environment}-accounts"
  database_name    = "reading_sound_game"
  runtime_db_user  = "reading_sound_app"
  migrator_db_user = "reading_sound_migrator"
  callback_url     = "${var.site_origin}/api/auth/callback"
  cognito_domain   = "https://${aws_cognito_user_pool_domain.parents.domain}.auth.${var.aws_region}.amazoncognito.com"
  database_iam_arn = "arn:${data.aws_partition.current.partition}:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_db_instance.accounts.resource_id}"
}

resource "aws_kms_key" "database" {
  description             = "Encrypted student progress database and automated backups"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "database" {
  name          = "alias/${local.name}-db"
  target_key_id = aws_kms_key.database.key_id
}

resource "aws_db_subnet_group" "accounts" {
  name       = local.name
  subnet_ids = local.database_subnet_ids
  depends_on = [aws_route_table_association.database]

  lifecycle {
    precondition {
      condition     = alltrue([for subnet in data.aws_subnet.database : subnet.vpc_id == var.vpc_id])
      error_message = "All database subnets must belong to the website's VPC."
    }
    precondition {
      condition     = length(toset(local.database_subnet_azs)) >= 2
      error_message = "Database subnets must span at least two availability zones."
    }
    precondition {
      condition     = data.aws_security_group.app.vpc_id == var.vpc_id
      error_message = "The application security group must belong to the same VPC."
    }
  }
}

resource "aws_security_group" "database" {
  name        = local.name
  description = "Private PostgreSQL; connections only from the website security group"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "database_from_app" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = var.app_security_group_id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "Website PostgreSQL access"
}

resource "aws_db_parameter_group" "accounts" {
  name_prefix = "${local.name}-"
  family      = "postgres17"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }
  parameter {
    name  = "log_statement"
    value = "none"
  }
  parameter {
    name  = "log_min_error_statement"
    value = "panic"
  }
  parameter {
    name  = "log_parameter_max_length"
    value = "0"
  }
  parameter {
    name  = "log_parameter_max_length_on_error"
    value = "0"
  }
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "accounts" {
  identifier                          = local.name
  engine                              = "postgres"
  engine_version                      = "17"
  engine_lifecycle_support            = "open-source-rds-extended-support-disabled"
  instance_class                      = var.db_instance_class
  db_name                             = local.database_name
  username                            = "reading_sound_admin"
  manage_master_user_password         = true
  iam_database_authentication_enabled = true
  allocated_storage                   = 20
  max_allocated_storage               = 100
  storage_type                        = "gp3"
  storage_encrypted                   = true
  kms_key_id                          = aws_kms_key.database.arn
  ca_cert_identifier                  = "rds-ca-rsa2048-g1"
  publicly_accessible                 = false
  db_subnet_group_name                = aws_db_subnet_group.accounts.name
  vpc_security_group_ids              = [aws_security_group.database.id]
  parameter_group_name                = aws_db_parameter_group.accounts.name
  port                                = 5432
  multi_az                            = var.multi_az
  backup_retention_period             = 7
  backup_window                       = "09:00-09:30"
  maintenance_window                  = "sun:10:00-sun:10:30"
  copy_tags_to_snapshot               = true
  auto_minor_version_upgrade          = true
  allow_major_version_upgrade         = false
  apply_immediately                   = false
  deletion_protection                 = var.deletion_protection
  skip_final_snapshot                 = false
  final_snapshot_identifier           = "${local.name}-${var.final_snapshot_suffix}"
  delete_automated_backups            = true
  performance_insights_enabled        = false
}

resource "aws_cognito_user_pool" "parents" {
  name                     = "${local.name}-parents"
  user_pool_tier           = "ESSENTIALS"
  deletion_protection      = var.deletion_protection ? "ACTIVE" : "INACTIVE"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"

  username_configuration {
    case_sensitive = false
  }
  admin_create_user_config {
    allow_admin_create_user_only = !var.parent_signup_enabled
  }
  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 1
  }
  software_token_mfa_configuration {
    enabled = true
  }
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }
  email_configuration {
    email_sending_account = var.ses_identity_arn == null ? "COGNITO_DEFAULT" : "DEVELOPER"
    source_arn            = var.ses_identity_arn
    from_email_address    = var.auth_email_from
  }
}

resource "aws_cognito_user_pool_client" "website" {
  name                                 = "${local.name}-server"
  user_pool_id                         = aws_cognito_user_pool.parents.id
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  callback_urls                        = [local.callback_url]
  default_redirect_uri                 = local.callback_url
  logout_urls                          = ["${var.site_origin}/"]
  supported_identity_providers         = ["COGNITO"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  read_attributes                      = ["email", "email_verified"]
  write_attributes                     = ["email"]
  access_token_validity                = 15
  id_token_validity                    = 15
  refresh_token_validity               = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_domain" "parents" {
  domain                = var.cognito_domain_prefix
  user_pool_id          = aws_cognito_user_pool.parents.id
  managed_login_version = 2
}

resource "aws_cognito_managed_login_branding" "parents" {
  user_pool_id                = aws_cognito_user_pool.parents.id
  client_id                   = aws_cognito_user_pool_client.website.id
  use_cognito_provided_values = true
}

resource "aws_iam_role_policy" "runtime" {
  name = "${local.name}-runtime"
  role = var.app_iam_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["rds-db:connect"]
        Resource = "${local.database_iam_arn}/${local.runtime_db_user}"
      },
      {
        Effect   = "Allow"
        Action   = ["cognito-idp:AdminDeleteUser", "cognito-idp:ListUsers"]
        Resource = aws_cognito_user_pool.parents.arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "migrator" {
  name = "${local.name}-migrator"
  role = var.trusted_migration_operator_arn == null ? var.migrator_iam_role_name : aws_iam_role.migrator[0].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["rds-db:connect"]
      Resource = "${local.database_iam_arn}/${local.migrator_db_user}"
    }]
  })
}

resource "aws_iam_role_policy" "privacy_email" {
  count = var.privacy_email_identity_arn == null ? 0 : 1
  name  = "${local.name}-privacy-email"
  role  = var.app_iam_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ses:SendEmail"]
      Resource = var.privacy_email_identity_arn
      Condition = {
        StringEquals = {
          "ses:FromAddress" = var.privacy_email_from
        }
        "ForAllValues:StringEquals" = {
          "ses:Recipients" = ["gina_underwood@yahoo.com", "jay.e.underwood@gmail.com"]
        }
      }
    }]
  })
}
