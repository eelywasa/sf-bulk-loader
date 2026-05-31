import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import { TierConfig, logRetentionFromDays } from './tier-config';

export interface NetworkStackProps extends cdk.StackProps {
  envName: string;
  vpcCidr: string;
  /**
   * Tier preset. Used to gate VPC flow logs (SFBL-355): they carry CloudWatch
   * ingestion cost, so they are enabled only on tiers that opt into enhanced
   * observability (`containerInsightsEnabled`) - bronze stays off.
   */
  tier: TierConfig;
}

/**
 * NetworkStack - VPC and network topology for the aws_hosted distribution.
 *
 * Layout:
 *   - 2 Availability Zones
 *   - Public subnets:   ALB, ECS Fargate tasks (public IPs - no NAT Gateway required)
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
  public readonly albSecurityGroup: ec2.SecurityGroup;
  public readonly backendServiceSecurityGroup: ec2.SecurityGroup;

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
          // RDS only. No route to the internet - accessible from within the VPC
          // exclusively. Using PRIVATE_ISOLATED rather than PRIVATE_WITH_EGRESS
          // ensures no NAT dependency is created even if natGateways is later
          // increased.
          name: 'Isolated',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
    });

    // S3 Gateway Endpoint - free; routes S3 traffic over the AWS backbone.
    // Eliminates internet egress charges for input CSV reads and result CSV writes.
    this.vpc.addGatewayEndpoint('S3GatewayEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });

    this.albSecurityGroup = new ec2.SecurityGroup(this, 'AlbSecurityGroup', {
      vpc: this.vpc,
      description: 'Allow HTTPS inbound to ALB',
    });
    this.albSecurityGroup.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(443), 'HTTPS');
    this.albSecurityGroup.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(80), 'HTTP redirect');

    this.backendServiceSecurityGroup = new ec2.SecurityGroup(this, 'BackendServiceSecurityGroup', {
      vpc: this.vpc,
      description: 'Shared security group for Bulk Loader ECS tasks',
    });
    this.backendServiceSecurityGroup.addIngressRule(this.albSecurityGroup, ec2.Port.tcp(8000), 'From ALB');

    // SFBL-355: VPC flow logs to CloudWatch for traffic auditing, gated on the
    // observability tier so disposable bronze envs don't pay the ingestion cost.
    // CDK provisions the log group, the delivery IAM role, and the flow log.
    if (props.tier.containerInsightsEnabled) {
      const flowLogGroup = new logs.LogGroup(this, 'VpcFlowLogGroup', {
        logGroupName: `/bulk-loader/${props.envName}/vpc-flow-logs`,
        retention: logRetentionFromDays(props.tier.logRetentionDays),
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      });
      this.vpc.addFlowLog('FlowLog', {
        destination: ec2.FlowLogDestination.toCloudWatchLogs(flowLogGroup),
        trafficType: ec2.FlowLogTrafficType.ALL,
      });
    }

    // TODO: Add Interface Endpoints for ECR, Secrets Manager, and SSM if a
    //       private-subnet architecture is adopted in future (~$21/month each).

    new cdk.CfnOutput(this, 'VpcId', {
      value: this.vpc.vpcId,
      description: 'VPC ID',
      exportName: `${this.stackName}-VpcId`,
    });
  }
}
