#!/usr/bin/env node
// Proposed .infrastructure/bin entrypoint. Synthesis only: never cdk deploy/publish.
// Keep the legacy synthesizer so unchanged helper/layer/data references do not move.
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import {BibleVerseStack} from '../lib/bibleverse-stack';
const app = new cdk.App();
new BibleVerseStack(app, 'bible-verse-stack', {
  env: {account: '664759038511', region: 'us-east-1'},
  synthesizer: new cdk.DefaultStackSynthesizer(),
});
