# Freshly generated, project-dedicated SSH key. The private key is written to
# terraform/.secrets/ (gitignored, mode 0600) and is also held in tf state.
resource "tls_private_key" "bastion" {
  algorithm = "ED25519"
}

resource "aws_key_pair" "bastion" {
  key_name   = "${var.project_name}-bastion"
  public_key = tls_private_key.bastion.public_key_openssh
}

resource "local_sensitive_file" "bastion_key" {
  content         = tls_private_key.bastion.private_key_openssh
  filename        = "${path.module}/.secrets/${var.project_name}-bastion.pem"
  file_permission = "0600"
}

# Latest Amazon Linux 2023 (ARM64, for the Graviton t4g.nano).
data "aws_ami" "al2023_arm" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-arm64"]
  }

  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}

resource "aws_instance" "bastion" {
  ami                         = data.aws_ami.al2023_arm.id
  instance_type               = var.bastion_instance_type
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.bastion.id]
  key_name                    = aws_key_pair.bastion.key_name
  associate_public_ip_address = true

  # Install the psql client so you can also run queries from the bastion itself.
  user_data = <<-EOF
    #!/bin/bash
    dnf install -y postgresql16
  EOF

  tags = { Name = "${var.project_name}-bastion" }
}

resource "aws_eip" "bastion" {
  instance = aws_instance.bastion.id
  domain   = "vpc"
  tags     = { Name = "${var.project_name}-bastion" }
}
