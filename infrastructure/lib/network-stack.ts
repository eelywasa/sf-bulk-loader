import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';

export interface NetworkStackProps extends cdk.StackProps {
  envName: string;
  vpcCidr: string;
}

/**
 * NetworkStack — VPC and network topology for the aws_hosted distribution.
 *
 * Layout:
 *   - 2 Availability Zones
 *   - Public subnets:   ALB, ECS Fargate tasks (public IPs — no NAT Gateway required)
 *   - Isolated subnets: RDS (no internet route; reachable from the VPC only)
 *
 * No NAT Gateway is provisioned. Fargate tasks receive public IPs and reach the
 * internet directly for Salesforce API calls. Security groups restrict all
 * inbound traffic to the ALB only, so the public IP exposure is equivalent to
 * a private-subnet deployment from an attack-surface perspective.
 *
 * The free S3 Gateway Endpoint routes all S3 traffic (input/output CSVs) over
 * the AWS backbone rather than the public internet, avoiding data-transfer charges.
 *
 * The VPC is exported and consumed by DataStack and BackendStack.
 */
export class NetworkStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;

  constructor(scope: Construct, id: string, props: NetworkStackProps) {
    super(scope, id, props);

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      ipAddresses: ec2.IpAddresses.cidr(props.vpcCidr),
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          // ALB and ECS Fargate tasks. Fargate tasks are assigned public IPs so
          // they can reach the Salesforce API without a NAT Gateway.
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
        {
          // RDS only. No route to the internet — accessible from within the VPC
          // exclusively. Using PRIVATE_ISOLATED rather than PRIVATE_WITH_EGRESS
          // ensures no NAT dependency is created even if natGateways is later
          // increased.
          name: 'Isolated',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
    });

    // S3 Gateway Endpoint — free; routes S3 traffic over the AWS backbone.
    // Eliminates internet egress charges for input CSV reads and result CSV writes.
    this.vpc.addGatewayEndpoint('S3GatewayEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });

    // TODO: Add VPC flow logs (CloudWatch Logs) for production traffic auditing.
    // TODO: Add Interface Endpoints for ECR, Secrets Manager, and SSM if a
    //       private-subnet architecture is adopted in future (~$21/month each).

    new cdk.CfnOutput(this, 'VpcId', {
      value: this.vpc.vpcId,
      description: 'VPC ID',
      exportName: `${this.stackName}-VpcId`,
    });
  }
}
