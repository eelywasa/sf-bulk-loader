# Application Load Balancer - TLS terminates here; the container receives
# plain HTTP on 8000.

resource "aws_lb" "main" {
  name_prefix        = "bl-"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [var.alb_security_group_id]
  subnets            = var.public_subnet_ids

  tags = {
    Name = "bulk-loader-${var.env_name}"
  }
}

# Target health uses /api/health/ready, which returns 503 when the DB is
# unreachable: a degraded task leaves rotation while ECS keeps it alive (the
# container's own /api/health/live check still passes), so it rejoins once
# the DB recovers. The legacy /api/health returns 200 even with a broken DB,
# which is why it is not used here.
resource "aws_lb_target_group" "backend" {
  name_prefix = "bl-"
  vpc_id      = var.vpc_id
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"

  health_check {
    path                = "/api/health/ready"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # Sticky sessions are not required - the backend is stateless (JWT auth).

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.backend_certificate_arn
  # The current AWS-recommended TLS 1.3/1.2 policy (CDK SslPolicy.RECOMMENDED_TLS).
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      protocol    = "HTTPS"
      port        = "443"
      status_code = "HTTP_301"
    }
  }
}

# --- DNS ---

data "aws_route53_zone" "main" {
  name         = var.hosted_zone_domain
  private_zone = false
}

resource "aws_route53_record" "backend" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.backend_domain_name
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}
