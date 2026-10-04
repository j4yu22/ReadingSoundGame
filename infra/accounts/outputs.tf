output "application_configuration" {
  description = "Non-secret configuration; collection remains disabled. Retrieve the Cognito client secret separately."
  value = {
    PUBLIC_ORIGIN                 = var.site_origin
    ACCOUNTS_ENABLED              = "false"
    ACCOUNT_REGISTRATION_OPEN     = "false"
    CHILD_DATA_COLLECTION_ENABLED = "false"
    AWS_REGION                    = var.aws_region
    DATABASE_URL                  = "postgresql+psycopg://${local.runtime_db_user}@${aws_db_instance.accounts.address}:5432/${local.database_name}"
    DATABASE_IAM_AUTH             = "true"
    DATABASE_SSL_ROOT_CERT        = "/app/certs/rds-global-bundle.pem"
    COGNITO_USER_POOL_ID          = aws_cognito_user_pool.parents.id
    COGNITO_CLIENT_ID             = aws_cognito_user_pool_client.website.id
    COGNITO_DOMAIN                = local.cognito_domain
    COGNITO_REDIRECT_URI          = local.callback_url
  }
}

output "database_bootstrap_secret_arn" {
  description = "Managed master secret ARN; only a separate bootstrap operator should read its value."
  value       = aws_db_instance.accounts.master_user_secret[0].secret_arn
}

output "database_address" {
  value = aws_db_instance.accounts.address
}
