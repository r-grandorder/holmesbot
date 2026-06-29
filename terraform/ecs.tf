resource "aws_ecs_cluster" "main" {
  name = var.project_name
}

resource "aws_cloudwatch_log_group" "bot" {
  name              = "/ecs/${var.project_name}"
  retention_in_days = 14
}

# Task definition for the bot container.
#
# The image tag is ":latest", which does NOT exist until the first CI build
# pushes it. That is fine: RegisterTaskDefinition never pulls the image (only a
# running task does), so `terraform apply` succeeds before any image exists.
# The service below will start tasks that keep retrying the pull until CI
# publishes :latest, after which CI takes over the running task def (see the
# lifecycle ignore_changes on the service).
resource "aws_ecs_task_definition" "bot" {
  family                   = "${var.project_name}-bot"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "bot"
      image     = "${aws_ecr_repository.bot.repository_url}:latest"
      essential = true

      portMappings = [
        {
          containerPort = 8080
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "GUILD_IDS", value = var.guild_ids },
        { name = "HEALTH_PORT", value = "8080" },
        { name = "LOG_LEVEL", value = "INFO" },
        { name = "ASSETS_BASE_URL", value = "https://${aws_s3_bucket.assets.bucket}.s3.${var.aws_region}.amazonaws.com" },
      ]

      secrets = [
        { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.database_url.arn },
        { name = "DISCORD_BOT_TOKEN", valueFrom = aws_ssm_parameter.discord_bot_token.arn },
        { name = "DISCORD_CLIENT_SECRET", valueFrom = aws_ssm_parameter.discord_client_secret.arn },
        { name = "DISCORD_APPLICATION_ID", valueFrom = aws_ssm_parameter.discord_application_id.arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.bot.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "bot"
        }
      }

      stopTimeout = 30

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"]
        interval    = 15
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    }
  ])
}

# Fargate service. No load balancer: the container healthCheck on /health is
# the sole health signal. Public subnet + public IP gives outbound to the
# Discord gateway and Atlas Academy without a NAT gateway.
#
# CI updates the running task definition on each deploy, so ignore drift on
# task_definition here (otherwise terraform would revert CI's rollouts).
resource "aws_ecs_service" "bot" {
  name            = "${var.project_name}-bot"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.bot.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.bot.id]
    assign_public_ip = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }
}
