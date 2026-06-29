data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# GitHub Actions OIDC: a keyless role the asset-refresh workflow assumes to
# write precomputed silhouettes to the S3 bucket. The S3 write permission for
# this role is attached in s3.tf.
# ---------------------------------------------------------------------------

# Fetch GitHub's OIDC TLS cert so we can pin its thumbprint. (AWS no longer
# enforces this for the well-known GitHub IdP, but the provider still requires it.)
data "tls_certificate" "github_oidc" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github_oidc.certificates[0].sha1_fingerprint]
}

# Trust policy: only the default branch of var.github_repo, with the STS audience.
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
