# Outputs for wiring up CI. Kept separate from outputs.tf.

output "github_ci_role_arn" {
  description = "ARN of the GitHub Actions OIDC deploy role. Set as the AWS_DEPLOY_ROLE_ARN repo variable."
  value       = aws_iam_role.github_ci.arn
}

output "ecs_cluster_name" {
  description = "ECS cluster name (workflow `cluster:` input)."
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name (workflow `service:` input)."
  value       = aws_ecs_service.bot.name
}

output "ecr_repository_url" {
  description = "ECR repository URL the bot image is pushed to."
  value       = aws_ecr_repository.bot.repository_url
}
