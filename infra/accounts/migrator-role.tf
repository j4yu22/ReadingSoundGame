resource "aws_iam_role" "migrator" {
  count                = var.trusted_migration_operator_arn == null ? 0 : 1
  name                 = var.migrator_iam_role_name
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = var.trusted_migration_operator_arn
      }
      Action = "sts:AssumeRole"
      Condition = {
        Bool = {
          "aws:MultiFactorAuthPresent" = "true"
        }
      }
    }]
  })
}
