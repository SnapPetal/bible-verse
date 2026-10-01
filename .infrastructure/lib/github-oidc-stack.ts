import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

const GITHUB_OIDC_HOST = 'token.actions.githubusercontent.com';

export interface GithubOidcStackProps extends cdk.StackProps {
  /** GitHub repository in "owner/repo" form. */
  readonly repository: string;
  /** Numeric owner and repository IDs, used by GitHub's immutable `sub` claim. */
  readonly repositoryIds: { readonly ownerId: string; readonly repoId: string };
  /**
   * GitHub Actions environment the deploy job runs in. Jobs that declare an
   * environment receive `sub = repo:<repo>:environment:<name>`.
   */
  readonly environment: string;
  readonly roleName: string;
  /** The account's existing GitHub OIDC provider (one per account). */
  readonly providerArn: string;
  /** CDK bootstrap qualifier. Default: hnb659fds */
  readonly cdkQualifier?: string;
}

/**
 * Least-privilege role for GitHub Actions CDK deploys. The role can only assume
 * the CDK bootstrap roles (which do the actual deployment) and read stack status.
 */
export class GithubOidcStack extends cdk.Stack {
  public readonly deployRole: iam.Role;

  constructor(scope: Construct, id: string, props: GithubOidcStackProps) {
    super(scope, id, props);

    const [owner, repo] = props.repository.split('/');
    const { ownerId, repoId } = props.repositoryIds;

    this.deployRole = new iam.Role(this, 'GithubDeployRole', {
      roleName: props.roleName,
      description: `GitHub Actions deploy role for ${props.repository} (${props.environment})`,
      maxSessionDuration: cdk.Duration.hours(1),
      assumedBy: new iam.WebIdentityPrincipal(props.providerArn, {
        StringEquals: {
          [`${GITHUB_OIDC_HOST}:aud`]: 'sts.amazonaws.com',
          [`${GITHUB_OIDC_HOST}:sub`]: [
            `repo:${owner}@${ownerId}/${repo}@${repoId}:environment:${props.environment}`,
            `repo:${props.repository}:environment:${props.environment}`,
          ],
        },
      }),
    });
    cdk.Tags.of(this.deployRole).add('Repository', props.repository);
    cdk.Tags.of(this.deployRole).add('Purpose', 'github-actions-cdk-deploy');
    cdk.Tags.of(this.deployRole).add('ManagedBy', 'CDK');

    const qualifier = props.cdkQualifier ?? 'hnb659fds';
    this.deployRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AssumeCdkBootstrapRoles',
        actions: ['sts:AssumeRole', 'sts:TagSession'],
        resources: [`arn:${this.partition}:iam::${this.account}:role/cdk-${qualifier}-*`],
      }),
    );
    this.deployRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadStackStatus',
        actions: ['cloudformation:DescribeStacks'],
        resources: [`arn:${this.partition}:cloudformation:${this.region}:${this.account}:stack/*`],
      }),
    );

    new cdk.CfnOutput(this, 'GithubDeployRoleArn', {
      value: this.deployRole.roleArn,
      description: 'AWS_ROLE_TO_ASSUME secret on the GitHub "production" environment',
    });
  }
}
