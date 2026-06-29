data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

# ---------------------------------------------------------------------------
# ECS task roles
# ---------------------------------------------------------------------------

# Shared assume-role policy: both the execution role and the task role are
# assumed by the ECS tasks service.
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: ECS agent uses this to pull the image, read secrets, and
# write logs (used before/around container start, not by app code).
resource "aws_iam_role" "ecs_task_execution" {
  name               = "${var.project_name}-ecs-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Inline grants beyond the managed policy: read the four SSM parameters,
# decrypt the SecureString ones, and write to the bot log group.
data "aws_iam_policy_document" "ecs_task_execution_inline" {
  statement {
    sid     = "ReadBotSecrets"
    actions = ["ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.database_url.arn,
      aws_ssm_parameter.discord_bot_token.arn,
      aws_ssm_parameter.discord_client_secret.arn,
      aws_ssm_parameter.discord_application_id.arn,
    ]
  }

  # SecureString params are encrypted with the AWS-managed aws/ssm key; the
  # execution role needs Decrypt to resolve them into container secrets.
  statement {
    sid       = "DecryptSecureString"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
  }

  statement {
    sid = "WriteBotLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.bot.arn}:*"]
  }
}

resource "aws_iam_role_policy" "ecs_task_execution_inline" {
  name   = "${var.project_name}-ecs-task-execution-inline"
  role   = aws_iam_role.ecs_task_execution.id
  policy = data.aws_iam_policy_document.ecs_task_execution_inline.json
}

# Task role: assumed by the running container (app code). Minimal for now;
# the bot reaches Postgres over the network, not via AWS APIs.
resource "aws_iam_role" "ecs_task" {
  name               = "${var.project_name}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# ---------------------------------------------------------------------------
# GitHub Actions OIDC: keyless deploy role
# ---------------------------------------------------------------------------

# Fetch GitHub's OIDC TLS cert so we can pin its thumbprint. (AWS no longer
# enforces this thumbprint for the well-known GitHub IdP, but the provider
# still requires the field.)
data "tls_certificate" "github_oidc" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github_oidc.certificates[0].sha1_fingerprint]
}

# Trust policy: only pushes to refs/heads/main of var.github_repo, with the
# STS audience, may assume this role.
data "aws_iam_policy_document" "github_ci_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_ci" {
  name               = "${var.project_name}-github-ci"
  assume_role_policy = data.aws_iam_policy_document.github_ci_assume.json
}

# CI permissions: ECR auth + push/pull, register/describe task defs, roll the
# bot service, and pass the two task roles to the new task definition.
data "aws_iam_policy_document" "github_ci" {
  statement {
    sid       = "EcrAuthToken"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPushPull"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
    resources = [aws_ecr_repository.bot.arn]
  }

  # RegisterTaskDefinition / DescribeTaskDefinition are not resource-scopable.
  statement {
    sid = "EcsTaskDefinition"
    actions = [
      "ecs:RegisterTaskDefinition",
      "ecs:DescribeTaskDefinition",
    ]
    resources = ["*"]
  }

  statement {
    sid = "EcsDeploy"
    actions = [
      "ecs:UpdateService",
      "ecs:DescribeServices",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ecs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:service/${var.project_name}/${var.project_name}-bot",
    ]
  }

  statement {
    sid     = "PassTaskRoles"
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.ecs_task_execution.arn,
      aws_iam_role.ecs_task.arn,
    ]
  }
}

resource "aws_iam_role_policy" "github_ci" {
  name   = "${var.project_name}-github-ci"
  role   = aws_iam_role.github_ci.id
  policy = data.aws_iam_policy_document.github_ci.json
}
