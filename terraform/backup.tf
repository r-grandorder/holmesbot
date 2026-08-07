# Private bucket for automated SQLite backups + a scoped IAM user for the self-hosted bot to
# upload them. Kept PRIVATE (unlike the public assets bucket) since these hold live game data.
#
# After `terraform apply`, copy the outputs into the bot's .env:
#   BACKUP_S3_BUCKET   = backup_bucket
#   AWS_ACCESS_KEY_ID  = backup_access_key_id
#   AWS_SECRET_ACCESS_KEY = backup_secret_access_key   (terraform output -raw backup_secret_access_key)

resource "aws_s3_bucket" "backups" {
  bucket = "${var.project_name}-backups-${data.aws_caller_identity.current.account_id}"
}

# Private: block all public access.
resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning so an overwrite/corruption can still be recovered.
resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Belt-and-suspenders lifecycle: the bot prunes to BACKUP_RETENTION, and S3 also expires anything
# older than 90 days (and old noncurrent versions) so nothing lingers/accrues cost indefinitely.
resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    id     = "expire-old-backups"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# A dedicated IAM user for the bot container (self-hosted, so no instance role available). Its
# policy is scoped to just this bucket, and just the actions the backup task needs.
resource "aws_iam_user" "backup" {
  name = "${var.project_name}-backup"
}

resource "aws_iam_user_policy" "backup" {
  name = "backup-rw"
  user = aws_iam_user.backup.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.backups.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.backups.arn
      },
    ]
  })
}

resource "aws_iam_access_key" "backup" {
  user = aws_iam_user.backup.name
}

output "backup_bucket" {
  value = aws_s3_bucket.backups.bucket
}

output "backup_access_key_id" {
  value = aws_iam_access_key.backup.id
}

output "backup_secret_access_key" {
  value     = aws_iam_access_key.backup.secret
  sensitive = true
}
