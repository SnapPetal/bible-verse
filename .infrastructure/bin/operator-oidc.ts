#!/usr/bin/env node
// Operator-only: never called by the application workflow.
// GitHubActionsBibleVerseProdDeploy was released from bible-verse-github-oidc-stack
// on 2026-11-14 (Retain, so the role stayed live) and imported into home-web's
// github-deploy-access app (stack bible-verse-entry-stack). The leftover stack
// holds only CDKMetadata. Do not recreate the role; the name already exists.
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';

new cdk.App();
