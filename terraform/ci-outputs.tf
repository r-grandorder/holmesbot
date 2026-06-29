# Outputs for wiring up CI.

output "github_ci_role_arn" {
  description = "ARN of the GitHub Actions OIDC role. Set as the AWS_DEPLOY_ROLE_ARN repo variable."
  value       = aws_iam_role.github_ci.arn
}
