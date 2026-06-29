# Secrets land in SSM Parameter Store (SecureString), sourced from tfvars. The
# ECS task (later slice) reads these at runtime; nothing secret ships in git.
locals {
  database_url = "postgresql://${var.db_username}:${urlencode(var.db_password)}@${aws_db_instance.main.address}:5432/${var.db_name}?sslmode=require"
}

resource "aws_ssm_parameter" "database_url" {
  name  = "/${var.project_name}/database-url"
  type  = "SecureString"
  value = local.database_url
}

resource "aws_ssm_parameter" "discord_bot_token" {
  name  = "/${var.project_name}/discord-bot-token"
  type  = "SecureString"
  value = var.discord_bot_token
}

resource "aws_ssm_parameter" "discord_client_secret" {
  name  = "/${var.project_name}/discord-client-secret"
  type  = "SecureString"
  value = var.discord_client_secret
}

resource "aws_ssm_parameter" "discord_application_id" {
  name  = "/${var.project_name}/discord-application-id"
  type  = "String"
  value = var.discord_application_id
}
