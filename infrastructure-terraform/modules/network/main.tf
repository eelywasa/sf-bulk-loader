# Network module - VPC and topology for the aws_hosted distribution.
# Mirrors infrastructure/lib/network-stack.ts:
#
#   - 2 Availability Zones
#   - Public subnets:   ALB, ECS Fargate tasks (public IPs - no NAT Gateway)
#   - Isolated subnets: RDS only (no internet route; reachable from the VPC only)
#
# No NAT Gateway is provisioned. Fargate tasks receive public IPs and reach
# the Salesforce API directly; security groups restrict all inbound traffic
# to the ALB only, so the public-IP exposure is equivalent to a
# private-subnet deployment from an attack-surface perspective.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  az_names = slice(data.aws_availability_zones.available.names, 0, 2)

  # Carve /24 subnets out of the VPC CIDR regardless of its prefix length,
  # matching the CDK's cidrMask: 24. Indexes 0-1 public, 2-3 isolated.
  subnet_newbits = 24 - tonumber(split("/", var.vpc_cidr)[1])
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "bulk-loader-${var.env_name}"
  }
}

# --- Public subnets: ALB + Fargate tasks ---

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "bulk-loader-${var.env_name}-igw"
  }
}

resource "aws_subnet" "public" {
  count = 2

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, local.subnet_newbits, count.index)
  availability_zone = local.az_names[count.index]
  # Fargate tasks are assigned public IPs so they can reach the Salesforce
  # API without a NAT Gateway.
  map_public_ip_on_launch = true

  tags = {
    Name = "bulk-loader-${var.env_name}-public-${local.az_names[count.index]}"
    Tier = "public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "bulk-loader-${var.env_name}-public"
  }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  count = 2

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- Isolated subnets: RDS only ---
# The route table deliberately carries no internet route (no IGW, no NAT) -
# the PRIVATE_ISOLATED equivalent. Reachable from within the VPC only.

resource "aws_subnet" "isolated" {
  count = 2

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, local.subnet_newbits, count.index + 2)
  availability_zone = local.az_names[count.index]

  tags = {
    Name = "bulk-loader-${var.env_name}-isolated-${local.az_names[count.index]}"
    Tier = "isolated"
  }
}

resource "aws_route_table" "isolated" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "bulk-loader-${var.env_name}-isolated"
  }
}

resource "aws_route_table_association" "isolated" {
  count = 2

  subnet_id      = aws_subnet.isolated[count.index].id
  route_table_id = aws_route_table.isolated.id
}

# --- S3 Gateway Endpoint ---
# Free; routes S3 traffic (input/output CSVs) over the AWS backbone rather
# than the public internet, avoiding data-transfer charges.

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id, aws_route_table.isolated.id]

  tags = {
    Name = "bulk-loader-${var.env_name}-s3-endpoint"
  }
}

data "aws_region" "current" {}

# --- Security groups ---
# Standalone rule resources (not inline ingress/egress blocks) per AWS
# provider v5+ guidance - rules can then be modified without recreating
# the group.

resource "aws_security_group" "alb" {
  name_prefix = "bulk-loader-${var.env_name}-alb-"
  description = "Allow HTTPS inbound to ALB"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "bulk-loader-${var.env_name}-alb"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http_redirect" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP redirect"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  description       = "Allow all outbound"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "backend_service" {
  name_prefix = "bulk-loader-${var.env_name}-backend-"
  description = "Shared security group for Bulk Loader ECS tasks"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "bulk-loader-${var.env_name}-backend"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Sourced from the ALB security group, never a CIDR - the tasks' public IPs
# are unreachable on 8000 from anywhere but the ALB.
resource "aws_vpc_security_group_ingress_rule" "backend_from_alb" {
  security_group_id            = aws_security_group.backend_service.id
  description                  = "From ALB"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "backend_all" {
  security_group_id = aws_security_group.backend_service.id
  description       = "Allow all outbound (Salesforce API, S3, SES)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- VPC flow logs (tier-gated) ---
# Enabled only on tiers that opt into enhanced observability - they carry
# CloudWatch ingestion cost, so bronze stays off.

resource "aws_cloudwatch_log_group" "flow_logs" {
  count = var.enable_flow_logs ? 1 : 0

  name              = "/bulk-loader/${var.env_name}/vpc-flow-logs"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "flow_logs_assume" {
  count = var.enable_flow_logs ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  count = var.enable_flow_logs ? 1 : 0

  name_prefix        = "bulk-loader-${var.env_name}-flow-logs-"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume[0].json
}

data "aws_iam_policy_document" "flow_logs_delivery" {
  count = var.enable_flow_logs ? 1 : 0

  # Writes are scoped to this log group's streams.
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.flow_logs[0].arn}:*"]
  }

  # The describes operate on log-group (not log-stream) ARNs; AWS's canonical
  # flow-logs delivery policy grants them on * and scoping them to a stream
  # pattern can make delivery fail with an access error (Codex review,
  # PR #105). Read-only metadata, so * is acceptable here.
  statement {
    actions = [
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "flow_logs" {
  count = var.enable_flow_logs ? 1 : 0

  name_prefix = "flow-logs-delivery-"
  role        = aws_iam_role.flow_logs[0].id
  policy      = data.aws_iam_policy_document.flow_logs_delivery[0].json
}

resource "aws_flow_log" "vpc" {
  count = var.enable_flow_logs ? 1 : 0

  vpc_id          = aws_vpc.main.id
  traffic_type    = "ALL"
  iam_role_arn    = aws_iam_role.flow_logs[0].arn
  log_destination = aws_cloudwatch_log_group.flow_logs[0].arn
}
