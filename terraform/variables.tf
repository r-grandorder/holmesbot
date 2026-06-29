variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name, used to prefix resource names"
  type        = string
  default     = "bunyanbot"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.42.0.0/16"
}

# --- Database ---
variable "db_name" {
  description = "Postgres database name"
  type        = string
  default     = "bunyanbot"
}

variable "db_username" {
  description = "Postgres master username"
  type        = string
  default     = "bunyanbot"
}

variable "db_password" {
  description = "Postgres master password (set in terraform.tfvars, gitignored)"
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS allocated storage in GB"
  type        = number
  default     = 20
}

# --- Bastion ---
variable "bastion_instance_type" {
  description = "EC2 instance type for the bastion (ARM/Graviton)"
  type        = string
  default     = "t4g.nano"
}

variable "ssh_ingress_cidr" {
  description = "CIDR allowed to SSH to the bastion. Default is open; security relies on key-only auth (no IP allowlist). Tighten if you want."
  type        = string
  default     = "0.0.0.0/0"
}

# --- Discord (secret values live in terraform.tfvars, which is gitignored) ---
variable "discord_application_id" {
  description = "Discord application ID (public identifier)"
  type        = string
}

variable "discord_bot_token" {
  description = "Discord bot token"
  type        = string
  sensitive   = true
}

variable "discord_client_secret" {
  description = "Discord OAuth2 client secret"
  type        = string
  sensitive   = true
}

# --- ECS / deploy CI ---
variable "github_repo" {
  description = "GitHub repo (owner/name) whose main branch may assume the CI deploy role via OIDC"
  type        = string
  default     = "r-grandorder/bunyanbot"
}

variable "container_cpu" {
  description = "Fargate task CPU units for the bot container"
  type        = number
  default     = 256
}

variable "container_memory" {
  description = "Fargate task memory (MiB) for the bot container"
  type        = number
  default     = 512
}

variable "desired_count" {
  description = "Number of bot tasks the ECS service runs"
  type        = number
  default     = 1
}

variable "guild_ids" {
  description = "Comma-separated Discord guild IDs for fast command sync (empty = global sync)"
  type        = string
  default     = ""
}
