#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import {BibleVerseStack} from "../lib/bibleverse-stack";
import {GithubOidcStack} from "../lib/github-oidc-stack";

const env = {account: '664759038511', region: 'us-east-1'};

const app = new cdk.App();
new BibleVerseStack(app, 'bible-verse-stack', {env});

new GithubOidcStack(app, 'bible-verse-github-oidc-stack', {
    env,
    terminationProtection: true,
    repository: 'SnapPetal/bible-verse',
    repositoryIds: {ownerId: '6395602', repoId: '395871434'},
    environment: 'production',
    roleName: 'GitHubActionsBibleVerseProdDeploy',
    providerArn: 'arn:aws:iam::664759038511:oidc-provider/token.actions.githubusercontent.com',
});
