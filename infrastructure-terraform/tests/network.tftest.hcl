# Network module assertions (SFBL-380 ACs).
# Mocked provider: no credentials, no cost. command = apply materialises
# generated ids so cross-resource references (SG -> SG) can be asserted.
#
# The "zero NAT gateways" AC is structural - no aws_nat_gateway resource
# exists anywhere in the configuration - and is enforced by the grep gate in
# the deployment guide's validation section (a tftest assertion cannot
# reference a resource type that is absent).

mock_provider "aws" {
  # The provider validates assume_role_policy as JSON even under mocks; the
  # generated mock string is not JSON, so pin a valid empty policy document.
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  # ARN-typed arguments are validated even under mocks - random mock strings
  # fail, so pin well-formed ARNs.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::111122223333:role/mock-role"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:eu-west-1:111122223333:log-group:/mock"
    }
  }

  mock_data "aws_availability_zones" {
    defaults = {
      names = ["eu-west-1a", "eu-west-1b", "eu-west-1c"]
    }
  }

  mock_data "aws_region" {
    defaults = {
      region = "eu-west-1"
    }
  }
}

variables {
  env_name           = "test"
  vpc_cidr           = "10.0.0.0/16"
  enable_flow_logs   = false
  log_retention_days = 7
}

run "topology_and_security_groups" {
  command = apply

  module {
    source = "./modules/network"
  }

  assert {
    condition     = length(aws_subnet.public) == 2 && length(aws_subnet.isolated) == 2
    error_message = "Expected exactly 2 public + 2 isolated subnets."
  }

  assert {
    condition     = aws_subnet.public[0].map_public_ip_on_launch && aws_subnet.public[1].map_public_ip_on_launch
    error_message = "Public subnets must assign public IPs (Fargate tasks reach Salesforce without NAT)."
  }

  assert {
    # Unset on isolated subnets -> null under mocks, so compare against true.
    condition     = aws_subnet.isolated[0].map_public_ip_on_launch != true && aws_subnet.isolated[1].map_public_ip_on_launch != true
    error_message = "Isolated subnets must not assign public IPs."
  }

  # /24s carved from the VPC CIDR (CDK cidrMask: 24).
  assert {
    condition     = alltrue([for s in concat(aws_subnet.public, aws_subnet.isolated) : endswith(s.cidr_block, "/24")])
    error_message = "All subnets must be /24 (CDK cidrMask parity)."
  }

  # The isolated route table must carry no internet route - the only route
  # resource in the module is the public default route.
  assert {
    condition     = aws_route.public_internet.route_table_id == aws_route_table.public.id
    error_message = "The only internet route must hang off the public route table."
  }

  # Backend SG ingress sourced from the ALB SG, never a CIDR.
  assert {
    condition     = aws_vpc_security_group_ingress_rule.backend_from_alb.referenced_security_group_id == aws_security_group.alb.id
    error_message = "Backend SG port 8000 must be sourced from the ALB security group."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.backend_from_alb.cidr_ipv4 == null
    error_message = "Backend SG port 8000 must not be CIDR-sourced."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.backend_from_alb.from_port == 8000 && aws_vpc_security_group_ingress_rule.backend_from_alb.to_port == 8000
    error_message = "Backend SG ingress must be port 8000 only."
  }

  # S3 gateway endpoint on both route tables. Containment, not count:
  # OpenTofu's mock apply can generate identical ids for both route tables,
  # collapsing the set to one element.
  assert {
    condition     = aws_vpc_endpoint.s3.vpc_endpoint_type == "Gateway" && contains(aws_vpc_endpoint.s3.route_table_ids, aws_route_table.public.id) && contains(aws_vpc_endpoint.s3.route_table_ids, aws_route_table.isolated.id)
    error_message = "S3 gateway endpoint must be associated with the public and isolated route tables."
  }

  # Flow logs absent when the tier gate is off.
  assert {
    condition     = length(aws_flow_log.vpc) == 0 && length(aws_cloudwatch_log_group.flow_logs) == 0
    error_message = "Flow logs must not be provisioned when enable_flow_logs is false."
  }
}

run "flow_logs_gated_on" {
  command = apply

  module {
    source = "./modules/network"
  }

  variables {
    enable_flow_logs   = true
    log_retention_days = 30
  }

  assert {
    condition     = length(aws_flow_log.vpc) == 1 && aws_flow_log.vpc[0].traffic_type == "ALL"
    error_message = "Flow logs (traffic_type ALL) must be provisioned when the tier enables them."
  }

  assert {
    condition     = aws_cloudwatch_log_group.flow_logs[0].name == "/bulk-loader/test/vpc-flow-logs" && aws_cloudwatch_log_group.flow_logs[0].retention_in_days == 30
    error_message = "Flow-log group must use the canonical name and tier retention."
  }
}
