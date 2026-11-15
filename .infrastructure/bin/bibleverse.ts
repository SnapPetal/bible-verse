#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import {BibleVerseStack} from "../lib/bibleverse-stack";

const env = {account: '664759038511', region: 'us-east-1'};

const app = new cdk.App();
// GitHubActionsBibleVerseProdDeploy was released from bible-verse-github-oidc-stack
// on 2026-11-14 and imported into home-web's github-deploy-access app
// (stack bible-verse-entry-stack). Do not synthesize that role here.
new BibleVerseStack(app, 'bible-verse-stack', {env});
