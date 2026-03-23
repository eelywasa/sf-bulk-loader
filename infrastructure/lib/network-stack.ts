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
 *   - Public subnets: ALB, NAT gateway
 *   - Private subnets: ECS tasks, RDS
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
      // Single NAT gateway is sufficient for non-production; add per-AZ for production HA.
      natGateways: 1,
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
        {
          name: 'Private',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          cidrMask: 24,
        },
      ],
    });

    // TODO: Add VPC flow logs (CloudWatch Logs) for production traffic auditing.
    // TODO: Add VPC endpoints for ECR (Docker/API), S3, Secrets Manager, and SSM
    //       to reduce NAT gateway costs and improve security posture.

    new cdk.CfnOutput(this, 'VpcId', {
      value: this.vpc.vpcId,
      description: 'VPC ID',
      exportName: `${this.stackName}-VpcId`,
    });
  }
}
