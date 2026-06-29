# Bot Fargate task SG. Defined now so RDS can reference it; the ECS service
# that uses it lands in a later slice. Egress-only (the bot dials out to the
# Discord gateway and Atlas Academy; nothing dials in).
resource "aws_security_group" "bot" {
  name        = "${var.project_name}-bot"
  description = "Bunyan bot Fargate task"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-bot" }
}

# Bastion SG. SSH is key-only (no password auth on the host), so the default
# source is open rather than pinned to a single (dynamic) home IP.
resource "aws_security_group" "bastion" {
  name        = "${var.project_name}-bastion"
  description = "SSH jump host for Postgres admin"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH (key-only auth)"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_ingress_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-bastion" }
}

# RDS SG. Postgres is reachable only from the bot task and the bastion.
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds"
  description = "Postgres, reachable only from the bot task and the bastion"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Postgres from bot task"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.bot.id]
  }

  ingress {
    description     = "Postgres from bastion"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.bastion.id]
  }

  tags = { Name = "${var.project_name}-rds" }
}
