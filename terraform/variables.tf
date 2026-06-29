variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name, used to prefix resource names. Kept as the existing value so the live assets bucket + OIDC role are not renamed/recreated."
  type        = string
  default     = "bunyanbot"
}

variable "github_repo" {
  description = "GitHub repo (owner/name) whose default branch may assume the asset-refresh OIDC role"
  type        = string
  default     = "r-grandorder/holmesbot"
}
