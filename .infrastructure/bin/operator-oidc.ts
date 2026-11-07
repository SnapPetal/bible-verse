#!/usr/bin/env node
// Operator-only: never called by the application workflow.
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import {GithubOidcStack} from '../lib/github-oidc-stack';
const app = new cdk.App();
const env = {account: '664759038511', region: 'us-east-1'};
new GithubOidcStack(app, 'bible-verse-github-oidc-stack', {
    env,
    terminationProtection: true,
    repository: 'SnapPetal/bible-verse',
    repositoryIds: {ownerId: '6395602', repoId: '395871434'},
    environment: 'production',
    roleName: 'GitHubActionsBibleVerseProdDeploy',
    providerArn: 'arn:aws:iam::664759038511:oidc-provider/token.actions.githubusercontent.com',
});

